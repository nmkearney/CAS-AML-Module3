"""``chatcde harvest metadata``: CDE records from BORIS into the catalogue.

The stage runs in two phases (decision 0008):

1. **Fetch** (network, resumable). Page through the BORIS search for the CDE
   name and save each reply as-is to ``<raw_dir>/boris/search/page-NNNN.json``.
   ``harvest_state.json`` records the query, the total and the pages done, so an
   interrupted run continues where it stopped. A finished fetch is not repeated
   unless ``force`` is given.
2. **Build** (offline, idempotent). Read every saved page, keep the CDE
   publications, map them with :mod:`chatcde.harvest.mapping` and replace the
   ``records`` and ``authors`` tables in one transaction. Work done by later
   stages is kept: ``has_fulltext`` (file download) and OpenAlex abstracts.

A run summary (counts, code version, query) goes to the log and to
``<raw_dir>/boris/harvest_summary.json``.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from chatcde import catalogue
from chatcde.config import BorisConfig, Config
from chatcde.harvest import mapping
from chatcde.harvest.boris import BorisClient
from chatcde.harvest.common import code_version, now, write_json
from chatcde.schemas import Author, Record, TextSource

log = logging.getLogger(__name__)


class HarvestError(Exception):
    """The stage cannot run as asked (e.g. no finished fetch to build from)."""


# ---------------------------------------------------------------------------
# State and summary
# ---------------------------------------------------------------------------


class FetchState(BaseModel):
    """Progress of the fetch phase, saved after every page."""

    query: str
    sort: str
    page_size: int
    total_elements: int
    total_pages: int
    pages_done: list[int] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    code_version: str

    @property
    def complete(self) -> bool:
        """True when every page has been saved."""
        return self.completed_at is not None


class HarvestSummary(BaseModel):
    """What one build wrote, with the denominators behind each count."""

    code_version: str
    built_at: datetime
    query: str
    fetched_at: datetime | None = Field(description="When the fetch finished")
    search_hits: int = Field(description="Unique items in the saved search pages")
    cde_items: int = Field(description="Items with a CDE affiliation or credit")
    cde_by_entity_type: dict[str, int]
    by_cde_link: dict[str, int] = Field(
        description="Records written, by how they name CDE (decision 0008)"
    )
    dropped: dict[str, int] = Field(description="Reason -> number of items left out")
    records: int
    authors: int
    removed: int = Field(description="Records in the old catalogue no longer found")
    with_doi: int
    with_abstract: int
    with_cde_unit: int
    by_pub_type: dict[str, int]


def boris_dir(raw_dir: Path) -> Path:
    """Folder for everything fetched from BORIS's search."""
    return raw_dir / "boris"


def _state_path(raw_dir: Path) -> Path:
    return boris_dir(raw_dir) / "harvest_state.json"


def _page_path(raw_dir: Path, page: int) -> Path:
    return boris_dir(raw_dir) / "search" / f"page-{page:04d}.json"


def load_state(raw_dir: Path) -> FetchState | None:
    """The saved fetch state, or None if no fetch has started."""
    path = _state_path(raw_dir)
    if not path.exists():
        return None
    return FetchState.model_validate_json(path.read_text(encoding="utf-8"))


def _save_state(raw_dir: Path, state: FetchState) -> None:
    write_json(_state_path(raw_dir), state.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Phase 1: fetch
# ---------------------------------------------------------------------------


def _search_result(reply: dict[str, Any]) -> dict[str, Any]:
    try:
        return reply["_embedded"]["searchResult"]
    except KeyError as exc:
        raise HarvestError(f"unexpected search reply, no searchResult: {exc}") from exc


def fetch_pages(
    client: BorisClient, cfg: BorisConfig, raw_dir: Path, *, force: bool = False
) -> FetchState:
    """Save every page of the CDE search under ``raw_dir``; resume if interrupted.

    Args:
        client: An open BORIS client.
        cfg: The ``boris`` config (query, sort and page size are checked
            against a saved state, since a change would shift the pages).
        raw_dir: Root of the raw cache (``data.raw_dir``).
        force: Delete earlier pages and state and fetch everything again.

    Returns:
        The final fetch state.

    Raises:
        HarvestError: A saved state was made with other search settings.
        NotOnVPNError: Not on the UniBe network/VPN.
    """
    if force and boris_dir(raw_dir).exists():
        log.info("--force: removing earlier search pages in %s", boris_dir(raw_dir))
        shutil.rmtree(boris_dir(raw_dir) / "search", ignore_errors=True)
        _state_path(raw_dir).unlink(missing_ok=True)

    state = load_state(raw_dir)
    if state is not None:
        settings = (state.query, state.sort, state.page_size)
        if settings != (cfg.cde_query, cfg.sort, cfg.page_size):
            raise HarvestError(
                f"the saved fetch used query/sort/page size {settings}, the config "
                "now says otherwise; run again with --force to start over"
            )
        if state.complete:
            log.info(
                "Fetch already complete (%d pages, %s); skipping. Use --force to "
                "fetch again.",
                state.total_pages,
                state.completed_at,
            )
            return state
        log.info(
            "Resuming fetch: %d of %d pages already saved",
            len(state.pages_done),
            state.total_pages,
        )

    client.preflight()
    page = 0
    warned = False
    while state is None or page < state.total_pages:
        if (
            state is not None
            and page in state.pages_done
            and _page_path(raw_dir, page).exists()
        ):
            page += 1
            continue
        fetched_at = now()
        reply = client.search_page(page)
        info = _search_result(reply)["page"]
        total, pages = int(info["totalElements"]), int(info["totalPages"])
        if state is None:
            state = FetchState(
                query=cfg.cde_query,
                sort=cfg.sort,
                page_size=cfg.page_size,
                total_elements=total,
                total_pages=pages,
                started_at=fetched_at,
                code_version=code_version(),
            )
            log.info("BORIS search: %d hits in %d pages", total, pages)
        elif total != state.total_elements and not warned:
            log.warning(
                "BORIS now reports %d hits, %d when this fetch started: pages may "
                "have shifted. Run with --force for a clean snapshot.",
                total,
                state.total_elements,
            )
            warned = True
        write_json(
            _page_path(raw_dir, page),
            {"page": page, "fetched_at": fetched_at.isoformat(), "reply": reply},
        )
        state.pages_done = sorted({*state.pages_done, page})
        _save_state(raw_dir, state)
        log.debug("saved page %d/%d", page + 1, state.total_pages)
        page += 1

    state.completed_at = now()
    _save_state(raw_dir, state)
    log.info("Fetched %d pages with %d requests", state.total_pages, client.requests)
    return state


# ---------------------------------------------------------------------------
# Phase 2: build
# ---------------------------------------------------------------------------


def iter_items(raw_dir: Path) -> Iterator[tuple[dict[str, Any], datetime]]:
    """Yield (item JSON, fetch time) for every item in the saved search pages."""
    for path in sorted((boris_dir(raw_dir) / "search").glob("page-*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(saved["fetched_at"])
        objects = _search_result(saved["reply"]).get("_embedded", {}).get("objects", [])
        for obj in objects:
            item = obj.get("_embedded", {}).get("indexableObject")
            if item and item.get("uuid"):
                yield item, fetched_at


def _existing(db_path: Path) -> dict[str, tuple[bool, str | None, str | None]]:
    """record_id -> (has_fulltext, abstract, abstract_source) in the catalogue now."""
    if not db_path.exists():
        return {}
    con = catalogue.connect(db_path, read_only=True)
    try:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        if "records" not in tables:
            return {}
        rows = con.execute(
            "SELECT record_id, has_fulltext, abstract, abstract_source FROM records"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _merge(rec: Record, old: tuple[bool, str | None, str | None] | None) -> Record:
    """Keep what later stages added: full-text flag and an OpenAlex abstract."""
    if old is None:
        return rec
    has_fulltext, abstract, source = old
    update: dict[str, Any] = {"has_fulltext": has_fulltext}
    if rec.abstract is None and source == TextSource.ABSTRACT_OPENALEX.value:
        update |= {
            "abstract": abstract,
            "abstract_source": TextSource.ABSTRACT_OPENALEX,
        }
    return rec.model_copy(update=update)


def build_catalogue(
    raw_dir: Path, db_path: Path, *, query: str, fetched_at: datetime | None = None
) -> HarvestSummary:
    """Rebuild ``records`` and ``authors`` from the saved search pages.

    Keeps items of entity type ``Publication``, not withdrawn, that name CDE
    (:func:`mapping.cde_link`). A record named only by the legacy
    ``dc.relation.organization`` is kept when :func:`mapping.confirms_org_link`
    confirms it, using the people who are CDE-affiliated on the explicit
    records (decision 0008). An item that appears on two pages is counted
    once, using its latest copy.

    Args:
        raw_dir: Root of the raw cache.
        db_path: The catalogue file (created if missing).
        query: The search query, for the summary.
        fetched_at: When the fetch finished, for the summary.

    Returns:
        Counts for the log and ``harvest_summary.json``.
    """
    items: dict[str, tuple[dict[str, Any], datetime]] = {}
    for item, at in iter_items(raw_dir):
        items[item["uuid"]] = (item, at)

    dropped: Counter[str] = Counter()
    by_entity: Counter[str] = Counter()
    by_link: Counter[str] = Counter()
    candidates: list[tuple[dict[str, Any], datetime, str]] = []
    for item, at in items.values():
        md = item.get("metadata", {})
        link = mapping.cde_link(md)
        if link is None:
            dropped["not_cde"] += 1
            continue
        etype = mapping.entity_type(item) or "(none)"
        by_entity[etype] += 1
        if etype != "Publication":
            dropped[f"entity_type:{etype}"] += 1
            continue
        if item.get("withdrawn") or item.get("inArchive") is False:
            dropped["withdrawn"] += 1
            continue
        candidates.append((item, at, link))

    cde_people = {
        name
        for item, _, link in candidates
        if link == "explicit"
        for name, here in mapping.people(item["metadata"]).items()
        if here
    }
    records: list[Record] = []
    authors: list[Author] = []
    old = _existing(db_path)
    for item, at, link in candidates:
        uuid = item["uuid"]
        if link == "org_link":
            why = mapping.confirms_org_link(item["metadata"], cde_people)
            if why is None:
                dropped["org_link_unconfirmed"] += 1
                continue
            link = f"org_link_{why}"
        try:
            rec = mapping.to_record(item, at)
            auth = mapping.to_authors(item)
        except ValueError as exc:  # pydantic.ValidationError is a ValueError
            log.warning("skipping %s: does not fit the schema: %s", uuid, exc)
            dropped["invalid"] += 1
            continue
        records.append(_merge(rec, old.get(uuid)))
        authors.extend(auth)
        by_link[link] += 1

    records.sort(key=lambda r: r.record_id)
    removed = len(set(old) - {r.record_id for r in records})
    con = catalogue.connect(db_path)
    try:
        catalogue.replace_tables(con, {"records": records, "authors": authors})
    finally:
        con.close()
    if removed:
        log.warning(
            "%d records in the old catalogue are no longer CDE publications in "
            "BORIS and were removed",
            removed,
        )
    return HarvestSummary(
        code_version=code_version(),
        built_at=now(),
        query=query,
        fetched_at=fetched_at,
        search_hits=len(items),
        cde_items=sum(by_entity.values()),
        cde_by_entity_type=dict(by_entity.most_common()),
        by_cde_link=dict(by_link.most_common()),
        dropped=dict(dropped.most_common()),
        records=len(records),
        authors=len(authors),
        removed=removed,
        with_doi=sum(r.doi is not None for r in records),
        with_abstract=sum(r.abstract is not None for r in records),
        with_cde_unit=sum(bool(r.cde_units) for r in records),
        by_pub_type=dict(Counter(r.pub_type for r in records).most_common()),
    )


# ---------------------------------------------------------------------------
# The whole stage
# ---------------------------------------------------------------------------


def harvest_metadata(
    cfg: Config,
    *,
    force: bool = False,
    offline: bool = False,
    client: BorisClient | None = None,
) -> HarvestSummary:
    """Fetch (unless ``offline``) and build; write and return the run summary.

    Args:
        cfg: The full config (``boris`` and ``data`` sections are used).
        force: Fetch every page again even if a finished fetch exists.
        offline: Skip the network and rebuild from the saved pages only.
        client: A client to use instead of a new one (for tests).

    Raises:
        HarvestError: ``offline`` without a finished fetch, or changed settings.
        NotOnVPNError: Not on the UniBe network/VPN.
    """
    raw_dir = cfg.data.raw_dir
    if offline:
        if force:
            raise HarvestError("--offline and --force cannot be combined")
        state = load_state(raw_dir)
        if state is None or not state.complete:
            raise HarvestError(
                f"no finished fetch in {boris_dir(raw_dir)}; run "
                "`chatcde harvest metadata` on the UniBe VPN first"
            )
    else:
        own = client is None
        client = client or BorisClient(cfg.boris)
        try:
            state = fetch_pages(client, cfg.boris, raw_dir, force=force)
        finally:
            if own:
                client.close()

    summary = build_catalogue(
        raw_dir, cfg.data.catalogue, query=state.query, fetched_at=state.completed_at
    )
    write_json(
        boris_dir(raw_dir) / "harvest_summary.json", summary.model_dump(mode="json")
    )
    log.info(
        "Catalogue: %d records, %d authors (%d search hits, %d CDE items) -> %s",
        summary.records,
        summary.authors,
        summary.search_hits,
        summary.cde_items,
        cfg.data.catalogue,
    )
    return summary
