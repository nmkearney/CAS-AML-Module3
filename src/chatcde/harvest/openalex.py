"""``chatcde harvest openalex``: abstracts and open-access links from OpenAlex.

The stage runs in two phases, like ``harvest metadata`` (decision 0010):

1. **Fetch** (network, resumable). Collect every DOI of every record: the
   publisher DOI (``Record.publisher_doi``) and BORIS's own DOI
   (``Record.doi``). Look them up in batches of up to 100 with one
   ``filter=doi:a|b|…`` request each, and save each reply
   as-is to ``<raw_dir>/openalex/lookups/batch-NNNN.json``.
   ``openalex_state.json`` lists the DOIs already looked up (found or not), so a
   rerun only asks for new ones; ``force`` starts over.
2. **Apply** (offline, idempotent). Rebuild each abstract from OpenAlex's
   inverted index, reject very short ones (``min_abstract_words``), and write it
   to ``records`` with ``abstract_source = abstract_openalex`` **only where BORIS
   has no abstract**. Abstracts filled by an earlier run are cleared first, so a
   new threshold takes effect. Open-access links go to
   ``<raw_dir>/openalex/oa_links.csv`` (no schema change), marked when they only
   point back to BORIS.

A summary with the denominators behind each count goes to the log and to
``<raw_dir>/openalex/openalex_summary.json``; it is the provenance record, since
``Record`` has no provenance field.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import ssl
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import certifi
import duckdb
import httpx
from pydantic import BaseModel, Field

from chatcde import __version__, catalogue
from chatcde.config import Config, OpenAlexConfig
from chatcde.harvest import mapping
from chatcde.harvest.common import code_version, now, write_json
from chatcde.harvest.mapping import doi_key
from chatcde.harvest.metadata import HarvestError
from chatcde.schemas import TextSource

log = logging.getLogger(__name__)

USER_AGENT = f"ChatCDE/{__version__} (CDE, University of Bern; read-only enrichment)"

#: Fields requested for each work (only root-level fields can be selected).
SELECT = "id,doi,language,abstract_inverted_index,open_access,best_oa_location"

#: Called after each batch with (batches done, batches to do).
ProgressFn = Callable[[int, int], None]


class OpenAlexError(Exception):
    """OpenAlex gave an unusable reply (HTTP error, bad JSON, retries used up)."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenAlexClient:
    """Sequential OpenAlex client with a fixed delay, retries and an optional key.

    Args:
        cfg: The ``openalex`` config section.
        transport: Optional httpx transport, for tests (``httpx.MockTransport``).
        sleep: Function used to pause; tests pass a recorder instead of
            :func:`time.sleep`.
    """

    def __init__(
        self,
        cfg: OpenAlexConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Open an HTTP client that verifies TLS with certifi's roots."""
        self.cfg = cfg
        self.base = cfg.api_base.rstrip("/")
        self._sleep = sleep
        self.requests = 0
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        key = cfg.api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(
            headers=headers,
            timeout=cfg.timeout_s,
            verify=ssl.create_default_context(cafile=certifi.where()),
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> OpenAlexClient:
        """Use the client as a context manager; it closes on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the client."""
        self.close()

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self.http.close()

    def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET ``path`` under the API base and return its JSON body.

        Waits ``request_delay_s`` before every attempt. Retries timeouts,
        connection errors, 429 and 5xx with a growing pause (or the server's
        ``Retry-After``, if it is longer).

        Raises:
            OpenAlexError: Any other HTTP error, bad JSON, or retries used up.
        """
        url = f"{self.base}/{path.lstrip('/')}"
        if self.cfg.mailto:
            params = {**params, "mailto": self.cfg.mailto}
        attempts = self.cfg.max_retries + 1
        problem = ""
        for attempt in range(1, attempts + 1):
            self._sleep(self.cfg.request_delay_s)
            self.requests += 1
            retry_after = 0.0
            try:
                r = self.http.get(url, params=params)
            except httpx.TransportError as exc:
                problem = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code < 400:
                    try:
                        return r.json()
                    except ValueError as exc:
                        raise OpenAlexError(f"invalid JSON from {url}: {exc}") from exc
                if r.status_code != 429 and r.status_code < 500:
                    raise OpenAlexError(
                        f"HTTP {r.status_code} for {url}: {r.text[:300]}"
                    )
                problem = f"HTTP {r.status_code}"
                try:
                    retry_after = float(r.headers.get("retry-after", 0))
                except ValueError:
                    retry_after = 0.0
            if attempt < attempts:
                pause = max(2.0**attempt, retry_after)
                log.warning(
                    "OpenAlex %s; retry %d/%d in %.0f s",
                    problem,
                    attempt,
                    self.cfg.max_retries,
                    pause,
                )
                self._sleep(pause)
        raise OpenAlexError(f"{problem} for {url} after {attempts} attempts")

    def works_by_doi(self, dois: list[str]) -> dict[str, Any]:
        """Look up up to 100 DOIs in one request; returns the raw reply.

        DOIs OpenAlex does not know are simply absent from ``results``.
        """
        if not 0 < len(dois) <= 100:
            raise ValueError(f"1 to 100 DOIs per request, got {len(dois)}")
        return self.get_json(
            "works",
            {
                "filter": "doi:" + "|".join(dois),
                "select": SELECT,
                "per_page": 100,
            },
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def abstract_from_index(inv: dict[str, list[int]] | None) -> str | None:
    """Rebuild an abstract from OpenAlex's ``abstract_inverted_index``.

    OpenAlex ships abstracts as word → positions (for legal reasons); the words
    are put back in position order.
    """
    if not inv:
        return None
    words = sorted((pos, word) for word, positions in inv.items() for pos in positions)
    return " ".join(w for _, w in words) or None


_LABELS = "abstract|summary|zusammenfassung|résumé|resumen"
#: "Abstract: ...", "Summary. ..." (any case, then punctuation or a space).
_LABEL = re.compile(rf"^(?:{_LABELS})\s*[:.]?\s+", re.I)
#: "ABSTRACTTerrestrial ...": an upper-case label glued to the first word.
_GLUED = re.compile(rf"^(?:{_LABELS.upper()})(?=[A-Z][a-z])")


def clean_abstract(text: str | None, min_words: int) -> str | None:
    """Tidy a rebuilt abstract; None if it is shorter than ``min_words`` words.

    Strips a leading "Abstract" label and collapses whitespace. Short texts are
    rejected because OpenAlex text-mines some abstracts, and very short ones
    are usually a heading or a sentence fragment rather than an abstract.
    """
    if not text:
        return None
    text = mapping.clean(text)
    text = _GLUED.sub("", _LABEL.sub("", text, count=1), count=1)
    return text if len(text.split()) >= min_words else None


class OALink(BaseModel):
    """An open-access copy of a work, as OpenAlex reports it."""

    openalex_id: str
    oa_status: str | None = None
    pdf_url: str | None = None
    landing_url: str | None = None
    license: str | None = None
    version: str | None = None
    host_is_boris: bool = Field(description="Points back to BORIS itself")


def _host(url: str | None) -> str:
    return (urlparse(url).hostname or "").lower() if url else ""


def oa_link(
    work: dict[str, Any], boris_hosts: Iterable[str], boris_prefixes: Iterable[str]
) -> OALink | None:
    """The best open-access location of a work, or None if it is not OA.

    Uses ``best_oa_location`` (its ``pdf_url``, else the landing page) and falls
    back to ``open_access.oa_url``. A link whose host is one of ``boris_hosts``
    or that is a DOI starting with one of ``boris_prefixes`` is marked
    ``host_is_boris``: it adds nothing to the files ``harvest files`` tried.
    """
    oa = work.get("open_access") or {}
    best = work.get("best_oa_location") or {}
    if not oa.get("is_oa") and not best:
        return None
    pdf = best.get("pdf_url")
    landing = best.get("landing_page_url") or oa.get("oa_url")
    if not pdf and not landing:
        return None
    hosts = {h.lower() for h in boris_hosts}
    target = pdf or landing
    host = _host(target)
    bare = doi_key(target) or ""
    is_boris = host in hosts or any(bare.startswith(p.lower()) for p in boris_prefixes)
    return OALink(
        openalex_id=work.get("id", ""),
        oa_status=oa.get("oa_status"),
        pdf_url=pdf,
        landing_url=landing,
        license=best.get("license"),
        version=best.get("version"),
        host_is_boris=is_boris,
    )


# ---------------------------------------------------------------------------
# Cache and state
# ---------------------------------------------------------------------------


def openalex_dir(raw_dir: Path) -> Path:
    """Folder for everything fetched from OpenAlex."""
    return raw_dir / "openalex"


def _state_path(raw_dir: Path) -> Path:
    return openalex_dir(raw_dir) / "openalex_state.json"


def _batch_paths(raw_dir: Path) -> list[Path]:
    return sorted((openalex_dir(raw_dir) / "lookups").glob("batch-*.json"))


class LookupState(BaseModel):
    """DOIs already looked up (found or not), saved after every batch."""

    dois_done: list[str] = Field(default_factory=list)
    batches: int = 0
    started_at: datetime
    updated_at: datetime
    code_version: str


def load_state(raw_dir: Path) -> LookupState | None:
    """The saved lookup state, or None if nothing has been looked up yet."""
    path = _state_path(raw_dir)
    if not path.exists():
        return None
    return LookupState.model_validate_json(path.read_text(encoding="utf-8"))


def fetch_works(
    client: OpenAlexClient,
    raw_dir: Path,
    dois: Iterable[str],
    *,
    batch_size: int,
    force: bool = False,
    limit: int | None = None,
    progress: ProgressFn | None = None,
) -> tuple[LookupState, int]:
    """Look up every DOI not looked up before; save each reply as a batch file.

    Args:
        client: An open OpenAlex client.
        raw_dir: Root of the raw cache (``data.raw_dir``).
        dois: DOIs to look up (normalised with :func:`doi_key`).
        batch_size: DOIs per request (at most 100).
        force: Delete earlier batches and state and look everything up again.
        limit: Look up at most this many new DOIs (for trial runs).
        progress: Called after each batch.

    Returns:
        The final state and the number of DOIs looked up in this run.
    """
    if force and openalex_dir(raw_dir).exists():
        log.info("--force: removing earlier OpenAlex lookups")
        shutil.rmtree(openalex_dir(raw_dir) / "lookups", ignore_errors=True)
        _state_path(raw_dir).unlink(missing_ok=True)

    state = load_state(raw_dir)
    if state is None:
        state = LookupState(
            started_at=now(), updated_at=now(), code_version=code_version()
        )
    done = set(state.dois_done)
    todo = sorted(set(dois) - done)
    if limit is not None:
        todo = todo[:limit]
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    log.info(
        "%d DOIs looked up earlier, %d to look up in %d requests",
        len(done),
        len(todo),
        len(batches),
    )
    for i, batch in enumerate(batches, 1):
        fetched_at = now()
        reply = client.works_by_doi(batch)
        n = state.batches + 1
        write_json(
            openalex_dir(raw_dir) / "lookups" / f"batch-{n:04d}.json",
            {"dois": batch, "fetched_at": fetched_at.isoformat(), "reply": reply},
        )
        done.update(batch)
        state.dois_done = sorted(done)
        state.batches = n
        state.updated_at = now()
        state.code_version = code_version()
        write_json(_state_path(raw_dir), state.model_dump(mode="json"))
        log.debug(
            "batch %d: %d DOIs, %d works", n, len(batch), len(reply.get("results", []))
        )
        if progress is not None:
            progress(i, len(batches))
    return state, len(todo)


def cached_works(raw_dir: Path) -> dict[str, dict[str, Any]]:
    """DOI → work, from every saved batch.

    If two works share a DOI, the one with an abstract is kept (else the first).
    """
    works: dict[str, dict[str, Any]] = {}
    for path in _batch_paths(raw_dir):
        saved = json.loads(path.read_text(encoding="utf-8"))
        for work in saved["reply"].get("results", []):
            key = doi_key(work.get("doi"))
            if not key:
                continue
            if key in works:
                log.debug("two OpenAlex works for %s", key)
                if works[key].get("abstract_inverted_index") or not work.get(
                    "abstract_inverted_index"
                ):
                    continue
            works[key] = work
    return works


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class OpenAlexSummary(BaseModel):
    """What one run did, with the denominators behind each count."""

    code_version: str
    run_at: datetime
    min_abstract_words: int
    requests: int
    records: int
    records_with_doi: int = Field(description="Records with any DOI")
    with_publisher_doi: int
    with_boris_doi: int
    dois_known: int = Field(description="Distinct DOIs to look up")
    dois_looked_up: int = Field(description="DOIs looked up so far (all runs)")
    dois_looked_up_this_run: int
    dois_found: int = Field(description="Looked-up DOIs that OpenAlex knows")
    records_found: int = Field(description="Records with at least one OpenAlex work")
    missing_abstract_before: int = Field(
        description="Records with no BORIS abstract (denominator for filling)"
    )
    abstract_filled: int
    abstract_filled_by_doi: dict[str, int] = Field(
        description="'publisher' or 'boris': which DOI gave the abstract"
    )
    abstract_none_in_openalex: int = Field(
        description="Missing abstract, work found, but OpenAlex has no abstract"
    )
    abstract_rejected_short: int
    missing_abstract_no_doi: int
    missing_abstract_not_found: int
    missing_abstract_after: int
    filled_by_pub_type: dict[str, int]
    oa_links: int
    oa_links_outside_boris: int
    oa_links_outside_boris_without_boris_file: int = Field(
        description="Non-BORIS OA links for records whose BORIS file was not "
        "downloaded (restricted, no_file, error or not tried)"
    )


def _row_dois(publisher: str | None, boris: str | None) -> list[tuple[str, str]]:
    """(kind, DOI) pairs to try, publisher DOI first."""
    out = []
    if publisher:
        out.append(("publisher", publisher))
    if boris and boris != publisher:
        out.append(("boris", boris))
    return out


def apply_openalex(
    con: duckdb.DuckDBPyConnection,
    raw_dir: Path,
    *,
    min_words: int,
    boris_hosts: Iterable[str],
    boris_prefixes: Iterable[str],
    pubs: dict[str, str],
    dois: set[str],
) -> dict[str, Any]:
    """Write OpenAlex abstracts to ``records`` and OA links to ``oa_links.csv``.

    Abstracts are written only where BORIS has none; abstracts from an earlier
    run are cleared first, in the same transaction, so the result depends only
    on the cache and ``min_words``.

    Args:
        con: An open, writable catalogue connection.
        raw_dir: Root of the raw cache.
        min_words: Shortest abstract accepted (``openalex.min_abstract_words``).
        boris_hosts: Hosts whose OA links point back to BORIS.
        boris_prefixes: DOI prefixes minted by BORIS.
        pubs: record_id → publisher DOI (``Record.publisher_doi``).
        dois: Every DOI of the current records, the denominator for "found".

    Returns:
        Counts for :class:`OpenAlexSummary`.
    """
    works = cached_works(raw_dir)
    catalogue.ensure_tables(con, ["records", "downloads"])
    rows = con.execute(
        "SELECT r.record_id, r.doi, r.pub_type, "
        "r.abstract IS NOT NULL AND r.abstract_source IS DISTINCT FROM ?, "
        "d.status "
        "FROM records r LEFT JOIN downloads d USING (record_id) ORDER BY r.record_id",
        [TextSource.ABSTRACT_OPENALEX.value],
    ).fetchall()

    c: Counter[str] = Counter()
    by_doi: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    updates: list[tuple[str, str, str]] = []
    links: list[dict[str, Any]] = []
    for record_id, boris_doi, pub_type, has_boris_abstract, dl_status in rows:
        tries = _row_dois(pubs.get(record_id), doi_key(boris_doi))
        found = [(kind, doi, works[doi]) for kind, doi in tries if doi in works]
        c["records_found"] += bool(found)

        if not has_boris_abstract:
            c["missing_before"] += 1
            if not tries:
                c["no_doi"] += 1
            elif not found:
                c["not_found"] += 1
            else:
                raw = [
                    (k, abstract_from_index(w.get("abstract_inverted_index")))
                    for k, _, w in found
                ]
                good = [(k, clean_abstract(t, min_words)) for k, t in raw if t]
                good = [(k, t) for k, t in good if t]
                if good:
                    kind, text = good[0]
                    src = TextSource.ABSTRACT_OPENALEX.value
                    updates.append((text, src, record_id))
                    by_doi[kind] += 1
                    by_type[pub_type] += 1
                elif any(t for _, t in raw):
                    c["rejected_short"] += 1
                else:
                    c["none_in_openalex"] += 1

        cands = [(doi, oa_link(w, boris_hosts, boris_prefixes)) for _, doi, w in found]
        cands = [(doi, link) for doi, link in cands if link]
        if cands:
            doi, link = next(
                ((d, lk) for d, lk in cands if not lk.host_is_boris), cands[0]
            )
            links.append(
                {
                    "record_id": record_id,
                    "doi": doi,
                    **link.model_dump(),
                    "boris_download_status": dl_status or "not_tried",
                }
            )

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            "UPDATE records SET abstract = NULL, abstract_source = NULL "
            "WHERE abstract_source = ?",
            [TextSource.ABSTRACT_OPENALEX.value],
        )
        if updates:
            con.executemany(
                "UPDATE records SET abstract = ?, abstract_source = ? "
                "WHERE record_id = ? AND abstract IS NULL",
                updates,
            )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise

    _write_links(openalex_dir(raw_dir) / "oa_links.csv", links)
    outside = [x for x in links if not x["host_is_boris"]]
    return {
        "records": len(rows),
        "records_with_doi": sum(
            bool(_row_dois(pubs.get(r[0]), doi_key(r[1]))) for r in rows
        ),
        "with_publisher_doi": sum(r[0] in pubs for r in rows),
        "with_boris_doi": sum(r[1] is not None for r in rows),
        "dois_found": len(dois & works.keys()),
        "records_found": c["records_found"],
        "missing_abstract_before": c["missing_before"],
        "abstract_filled": len(updates),
        "abstract_filled_by_doi": dict(by_doi.most_common()),
        "abstract_none_in_openalex": c["none_in_openalex"],
        "abstract_rejected_short": c["rejected_short"],
        "missing_abstract_no_doi": c["no_doi"],
        "missing_abstract_not_found": c["not_found"],
        "missing_abstract_after": c["missing_before"] - len(updates),
        "filled_by_pub_type": dict(by_type.most_common()),
        "oa_links": len(links),
        "oa_links_outside_boris": len(outside),
        "oa_links_outside_boris_without_boris_file": sum(
            x["boris_download_status"] != "downloaded" for x in outside
        ),
    }


LINK_COLUMNS = [
    "record_id",
    "doi",
    "openalex_id",
    "oa_status",
    "pdf_url",
    "landing_url",
    "license",
    "version",
    "host_is_boris",
    "boris_download_status",
]


def _write_links(path: Path, links: list[dict[str, Any]]) -> None:
    """Write the OA-link table atomically (temp file, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LINK_COLUMNS)
        w.writeheader()
        w.writerows(links)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# The whole stage
# ---------------------------------------------------------------------------


def harvest_openalex(
    cfg: Config,
    *,
    force: bool = False,
    offline: bool = False,
    limit: int | None = None,
    client: OpenAlexClient | None = None,
    progress: ProgressFn | None = None,
) -> OpenAlexSummary:
    """Look up every record's DOIs in OpenAlex (unless ``offline``), then apply.

    Args:
        cfg: The full config (``openalex`` and ``data`` sections are used).
        force: Look every DOI up again, even those cached.
        offline: Skip the network and apply the cached lookups only.
        limit: Look up at most this many new DOIs (for trial runs).
        client: A client to use instead of a new one (for tests).
        progress: Called after each batch, e.g. to drive a progress bar.

    Returns:
        The summary, also written to ``<raw_dir>/openalex/openalex_summary.json``.

    Raises:
        HarvestError: No records yet, ``offline`` without a cache, or
            ``offline`` with ``force``.
        OpenAlexError: OpenAlex failed (batches saved so far are kept).
    """
    raw_dir, db = cfg.data.raw_dir, cfg.data.catalogue
    if offline and force:
        raise HarvestError("--offline and --force cannot be combined")
    if offline and load_state(raw_dir) is None:
        raise HarvestError(
            f"no OpenAlex lookups in {openalex_dir(raw_dir)}; run "
            "`chatcde harvest openalex` without --offline first"
        )

    con = catalogue.connect(db)
    try:
        catalogue.ensure_tables(con, ["records"])
        if "publisher_doi" not in catalogue.columns(con, "records"):
            raise HarvestError(
                f"{db} predates Record.publisher_doi; run "
                "`chatcde harvest metadata --offline` first"
            )
        recs = con.execute(
            "SELECT record_id, doi, publisher_doi FROM records"
        ).fetchall()
        if not recs:
            raise HarvestError(
                f"no records in {db}; run `chatcde harvest metadata` first"
            )
        pubs = {r[0]: k for r in recs if (k := doi_key(r[2]))}
        dois = set(pubs.values()) | {k for k in (doi_key(r[1]) for r in recs) if k}

        requests, this_run = 0, 0
        if not offline:
            own = client is None
            client = client or OpenAlexClient(cfg.openalex)
            try:
                _, this_run = fetch_works(
                    client,
                    raw_dir,
                    dois,
                    batch_size=cfg.openalex.batch_size,
                    force=force,
                    limit=limit,
                    progress=progress,
                )
                requests = client.requests
            finally:
                if own:
                    client.close()
        state = load_state(raw_dir)
        looked_up = len(set(state.dois_done) & dois) if state else 0

        counts = apply_openalex(
            con,
            raw_dir,
            min_words=cfg.openalex.min_abstract_words,
            boris_hosts=cfg.openalex.boris_hosts,
            boris_prefixes=cfg.openalex.boris_doi_prefixes,
            pubs=pubs,
            dois=dois,
        )
    finally:
        con.close()

    summary = OpenAlexSummary(
        code_version=code_version(),
        run_at=now(),
        min_abstract_words=cfg.openalex.min_abstract_words,
        requests=requests,
        dois_known=len(dois),
        dois_looked_up=looked_up,
        dois_looked_up_this_run=this_run,
        **counts,
    )
    write_json(
        openalex_dir(raw_dir) / "openalex_summary.json",
        summary.model_dump(mode="json"),
    )
    log.info(
        "OpenAlex: %d of %d missing abstracts filled; %d OA links (%d outside "
        "BORIS) -> %s",
        summary.abstract_filled,
        summary.missing_abstract_before,
        summary.oa_links,
        summary.oa_links_outside_boris,
        openalex_dir(raw_dir),
    )
    return summary
