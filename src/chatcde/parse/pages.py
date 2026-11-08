"""``chatcde parse``: each downloaded PDF → one ``PageText`` per page.

The stage runs in two passes (decision 0014):

1. **Text layer.** For each record whose main PDF was downloaded
   (``downloads.status = 'downloaded'``), read every page with PyMuPDF, class it
   (:class:`~chatcde.parse.pdf.PageClass`), detect its language and write all
   pages to the ``pages`` table. Pages classed ``ocr`` or ``garbled`` keep their
   text-layer text for now and are listed as *pending* in ``parse_state``.
2. **OCR.** Each pending page is rendered and transcribed by the ``vision``
   model, up to ``llm.max_concurrency`` requests at a time. Answers are cached
   (:class:`~chatcde.parse.ocr.OcrCache`). With ``ocr=False`` only cached
   answers are used and the rest stay pending for a later run. After
   ``parse.max_consecutive_failures`` endpoint failures in a row the pass stops,
   so an unreachable endpoint does not take hours of retries. Pages the model
   cannot read (loops, rejected scripts) fail on their own and do not count.

Resumable and idempotent: the Track A side table ``parse_state`` keeps, per
record, the PDF's SHA-256, :data:`~chatcde.parse.pdf.PARSER_VERSION`, the
classification settings, page counts by class, the pending OCR pages and a
status (``done``, ``needs_ocr`` or ``error``). A rerun skips records whose PDF,
parser version and settings are unchanged and whose pages are all stored;
``needs_ocr`` records only go through the OCR pass again; ``error`` records are
retried. ``force`` re-reads every PDF (cached OCR answers are still used). Each
record is written in its own transaction. Records whose PDF is no longer
downloaded lose their pages.

A summary with counts and their denominators goes to the log and to
``<data.text_dir>/parse_summary.json``.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from chatcde import catalogue
from chatcde.config import Config
from chatcde.harvest.common import code_version, now, write_json
from chatcde.harvest.files import file_path
from chatcde.parse.language import detect_language
from chatcde.parse.ocr import (
    CacheKey,
    EndpointDown,
    OcrCache,
    OcrError,
    check_answer,
    load_prompt,
    ocr_page,
    png_sha256,
)
from chatcde.parse.pdf import (
    PARSER_VERSION,
    PageClass,
    classify,
    extract_pages,
    open_pdf,
    parser_name,
    render_png,
    take_warnings,
)
from chatcde.schemas import PageText, Provenance
from chatcde.store.endpoint import EndpointClient

log = logging.getLogger(__name__)

#: Per-record state of the parse stage (Track A side table, not in schemas.py).
STATE_TABLE = "parse_state"
_STATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
  record_id VARCHAR NOT NULL,
  sha256 VARCHAR,
  parser_version VARCHAR NOT NULL,
  settings VARCHAR NOT NULL,
  n_pages BIGINT NOT NULL,
  n_text BIGINT NOT NULL,
  n_ocr BIGINT NOT NULL,
  n_garbled BIGINT NOT NULL,
  n_blank BIGINT NOT NULL,
  n_ocr_done BIGINT NOT NULL,
  ocr_pending BIGINT[] NOT NULL,
  status VARCHAR NOT NULL,
  error VARCHAR,
  code_version VARCHAR NOT NULL,
  parsed_at TIMESTAMP NOT NULL
)
"""

#: ``parse`` settings that change what pass 1 writes; a change re-reads the PDF.
_PASS1_SETTINGS = (
    "min_chars",
    "min_image_coverage",
    "min_drawings",
    "max_garbage_ratio",
    "min_alnum_ratio",
    "languages",
    "min_chars_language",
    "min_language_confidence",
    "language_sample_chars",
)

DONE, NEEDS_OCR, ERROR = "done", "needs_ocr", "error"

#: Called as ``progress(pass_name, done, total)``; pass_name is "text" or "ocr".
ProgressFn = Callable[[str, int, int], None]


class ParseError(Exception):
    """The catalogue is not ready for parsing (no downloads yet, no prompt)."""


class ParseSummary(BaseModel):
    """State of the ``pages`` table after a run, with its denominators."""

    code_version: str
    parser: str = Field(description="PDF library and version")
    parser_version: str
    run_at: datetime
    records: int = Field(description="Records with a downloaded PDF")
    parsed: int = Field(description="Records whose PDF was read in this run")
    skipped: int = Field(description="Records already parsed, not read again")
    removed: int = Field(description="Records dropped: PDF no longer downloaded")
    errors: int = Field(description="Records that failed in this run")
    pages: int = Field(description="Rows in the pages table")
    by_class: dict[str, int] = Field(description="Pages by class, all records")
    by_status: dict[str, int] = Field(description="Records by parse status")
    ocr_requests: int = Field(description="Vision requests sent in this run")
    ocr_cached: int = Field(description="Pending pages filled from the cache")
    ocr_failed: int = Field(description="Vision requests that failed in this run")
    ocr_stopped: bool = Field(description="OCR pass stopped after repeated failures")
    ocr_pages: int = Field(description="Pages whose text came from the vision model")
    ocr_pending: int = Field(description="Pages still waiting for OCR")
    by_language: dict[str, int] = Field(description="Pages by detected language")
    boris_vs_detected: dict[str, dict[str, int]] = Field(
        description="BORIS first language -> main detected language -> records"
    )


def _settings_json(cfg: Config) -> str:
    """The pass-1 settings as stable JSON, stored per record."""
    s = cfg.parse.model_dump()
    return json.dumps({k: s[k] for k in _PASS1_SETTINGS}, sort_keys=True)


def _provenance(
    version: str, *, model: str | None = None, prompt: str | None = None
) -> Provenance:
    """Provenance for a page: code version, time, and model/prompt for OCR pages."""
    return Provenance(
        code_version=version, created_at=now(), model=model, prompt_version=prompt
    )


# ---------------------------------------------------------------------------
# Pass 1: text layer
# ---------------------------------------------------------------------------


@dataclass
class _Pass1Result:
    """What reading one PDF produced."""

    pages: list[PageText]
    counts: Counter[str]
    pending: list[int]


def read_record(cfg: Config, record_id: str, version: str) -> _Pass1Result:
    """Read one record's PDF: classed pages with text-layer text and language.

    Raises:
        FileNotFoundError: The PDF is not in ``data.files_dir``.
        ValueError: It is encrypted or not a PDF.
        RuntimeError: PyMuPDF cannot read it (its errors derive from this).
    """
    path = file_path(cfg.data.files_dir, record_id)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing; run `chatcde harvest files`")
    doc = open_pdf(path)
    try:
        raw_pages = extract_pages(doc, cfg.parse)
    finally:
        doc.close()
    warnings = take_warnings()
    if warnings:
        log.debug("%s: MuPDF warnings: %s", record_id, warnings.replace("\n", "; "))
    pages, counts, pending = [], Counter[str](), []
    for raw in raw_pages:
        cls = classify(raw, cfg.parse)
        counts[cls.value] += 1
        if cls.needs_ocr:
            pending.append(raw.page)
        language = (
            None if cls is PageClass.GARBLED else detect_language(raw.text, cfg.parse)
        )
        pages.append(
            PageText(
                record_id=record_id,
                page=raw.page,
                text=raw.text,
                language=language,
                ocr=False,
                provenance=_provenance(version),
            )
        )
    return _Pass1Result(pages, counts, pending)


def _write_state(
    con: duckdb.DuckDBPyConnection,
    record_id: str,
    *,
    sha256: str | None,
    settings: str,
    counts: Counter[str],
    n_pages: int,
    n_ocr_done: int,
    pending: list[int],
    status: str,
    error: str | None,
    version: str,
) -> None:
    """Replace one record's ``parse_state`` row (inside the caller's transaction)."""
    con.execute(f"DELETE FROM {STATE_TABLE} WHERE record_id = ?", [record_id])
    con.execute(
        f"INSERT INTO {STATE_TABLE} VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            record_id,
            sha256,
            PARSER_VERSION,
            settings,
            n_pages,
            counts.get(PageClass.TEXT.value, 0),
            counts.get(PageClass.OCR.value, 0),
            counts.get(PageClass.GARBLED.value, 0),
            counts.get(PageClass.BLANK.value, 0),
            n_ocr_done,
            pending,
            status,
            error,
            version,
            now().replace(tzinfo=None),
        ],
    )


def _transaction(con: duckdb.DuckDBPyConnection, work: Callable[[], None]) -> None:
    """Run ``work`` in one transaction; roll back on any error."""
    con.execute("BEGIN TRANSACTION")
    try:
        work()
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _store_pass1(
    con: duckdb.DuckDBPyConnection,
    record_id: str,
    sha256: str | None,
    settings: str,
    version: str,
    result: _Pass1Result | None,
    error: str | None,
) -> None:
    """Replace a record's pages and state with the pass-1 result (or its error)."""

    def work() -> None:
        con.execute("DELETE FROM pages WHERE record_id = ?", [record_id])
        if result is None:
            _write_state(
                con,
                record_id,
                sha256=sha256,
                settings=settings,
                counts=Counter(),
                n_pages=0,
                n_ocr_done=0,
                pending=[],
                status=ERROR,
                error=error,
                version=version,
            )
            return
        catalogue.insert(con, "pages", result.pages)
        _write_state(
            con,
            record_id,
            sha256=sha256,
            settings=settings,
            counts=result.counts,
            n_pages=len(result.pages),
            n_ocr_done=0,
            pending=result.pending,
            status=NEEDS_OCR if result.pending else DONE,
            error=None,
            version=version,
        )

    _transaction(con, work)


# ---------------------------------------------------------------------------
# Pass 2: OCR
# ---------------------------------------------------------------------------


@dataclass
class _OcrRecord:
    """OCR progress of one record during pass 2."""

    record_id: str
    pending: list[int]
    cache: OcrCache
    results: dict[int, str] = field(default_factory=dict)
    failures: dict[int, str] = field(default_factory=dict)
    handled: int = 0  # pending pages answered, failed or left for later
    finished: bool = False

    @property
    def complete(self) -> bool:
        """Every pending page has been dealt with."""
        return self.handled >= len(self.pending)


def _ocr_jobs(
    cfg: Config, records: list[_OcrRecord], model: str, prompt_name: str
) -> Iterator[tuple[_OcrRecord, int, CacheKey | None, bytes | None]]:
    """Render pending pages one at a time, record by record.

    Yields ``(record, page, key, png)``; ``key`` and ``png`` are ``None`` when the
    PDF cannot be opened (the page then counts as failed). Pages are rendered
    lazily so a long scanned book never sits in memory as images.
    """
    for rec in records:
        path = file_path(cfg.data.files_dir, rec.record_id)
        try:
            doc = open_pdf(path)
        except (OSError, ValueError, RuntimeError) as exc:
            rec.failures = {p: f"cannot open PDF: {exc}" for p in rec.pending}
            for p in rec.pending:
                yield rec, p, None, None
            continue
        try:
            for p in rec.pending:
                png = render_png(
                    doc[p - 1], dpi=cfg.parse.ocr_dpi, max_px=cfg.parse.ocr_max_px
                )
                yield rec, p, CacheKey(p, png_sha256(png), model, prompt_name), png
        finally:
            doc.close()
            take_warnings()


def _finish_ocr_record(
    con: duckdb.DuckDBPyConnection, cfg: Config, rec: _OcrRecord, version: str
) -> None:
    """Write a record's OCR answers to ``pages`` and update its state."""
    model, prompt = cfg.models.vision, cfg.parse.ocr_prompt
    remaining = [p for p in rec.pending if p not in rec.results]
    error = next(iter(rec.failures.values()), None) if remaining else None
    if rec.failures:
        log.warning(
            "%s: OCR failed on %d page(s): %s", rec.record_id, len(rec.failures), error
        )

    def work() -> None:
        for page, text in sorted(rec.results.items()):
            con.execute(
                "DELETE FROM pages WHERE record_id = ? AND page = ?",
                [rec.record_id, page],
            )
            catalogue.insert(
                con,
                "pages",
                [
                    PageText(
                        record_id=rec.record_id,
                        page=page,
                        text=text,
                        language=detect_language(text, cfg.parse),
                        ocr=True,
                        provenance=_provenance(version, model=model, prompt=prompt),
                    )
                ],
            )
        con.execute(
            f"UPDATE {STATE_TABLE} SET n_ocr_done = n_ocr_done + ?, ocr_pending = ?, "
            "status = ?, error = ?, code_version = ?, parsed_at = ? "
            "WHERE record_id = ?",
            [
                len(rec.results),
                remaining,
                NEEDS_OCR if remaining else DONE,
                error,
                version,
                now().replace(tzinfo=None),
                rec.record_id,
            ],
        )

    _transaction(con, work)
    rec.finished = True


@dataclass
class _OcrStats:
    """Counters of one OCR pass."""

    requests: int = 0
    cached: int = 0
    failed: int = 0
    stopped: bool = False


def run_ocr(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    record_ids: list[str],
    *,
    ocr: bool,
    client: EndpointClient | None,
    version: str,
    progress: ProgressFn | None = None,
) -> _OcrStats:
    """Pass 2: fill pending pages of ``record_ids`` from the cache or the model.

    Args:
        con: Open catalogue.
        cfg: The full config.
        record_ids: Records in ``needs_ocr`` state to work on.
        ocr: Send uncached pages to the vision model; if False, use the cache only.
        client: Endpoint client to use (tests); otherwise one is opened on the
            first uncached page.
        version: Code version for provenance.
        progress: Called after each pending page.

    Raises:
        ParseError: The OCR prompt file is missing.
    """
    stats = _OcrStats()
    rows = (
        con.execute(
            f"SELECT record_id, ocr_pending FROM {STATE_TABLE} "
            "WHERE status = ? AND record_id IN (SELECT unnest(?::VARCHAR[])) "
            "ORDER BY record_id",
            [NEEDS_OCR, record_ids],
        ).fetchall()
        if record_ids
        else []
    )
    cache_dir = cfg.data.text_dir / "ocr"
    records = [
        _OcrRecord(rid, list(pending), OcrCache(cache_dir, rid))
        for rid, pending in rows
        if pending
    ]
    if not ocr:
        # Cache only: a record without cached answers has nothing to fill, so
        # its pages are not rendered at all (and its state is left as it is).
        records = [r for r in records if len(r.cache)]
    total = sum(len(r.pending) for r in records)
    if not total:
        return stats
    model, prompt_name = cfg.models.vision, cfg.parse.ocr_prompt
    prompt = ""
    if ocr:
        try:
            prompt = load_prompt(cfg.prompts.dir, prompt_name)
        except FileNotFoundError as exc:
            raise ParseError(f"OCR prompt not found: {exc.filename}") from exc
    log.info(
        "OCR pass: %d pages in %d records (%s)",
        total,
        len(records),
        f"model {model}, prompt {prompt_name}" if ocr else "cache only, --no-ocr",
    )

    own = client is None
    done = 0
    streak = 0
    in_flight: dict[Future[str], tuple[_OcrRecord, int, CacheKey]] = {}
    cap = 2 * cfg.llm.max_concurrency
    jobs = _ocr_jobs(cfg, records, model, prompt_name)
    exhausted = False

    def handled(rec: _OcrRecord) -> None:
        nonlocal done
        rec.handled += 1
        done += 1
        if progress:
            progress("ocr", done, total)
        if rec.complete and not rec.finished:
            _finish_ocr_record(con, cfg, rec, version)

    pool = ThreadPoolExecutor(max_workers=cfg.llm.max_concurrency)
    try:
        while True:
            while not stats.stopped and not exhausted and len(in_flight) < cap:
                try:
                    rec, page, key, png = next(jobs)
                except StopIteration:
                    exhausted = True
                    break
                if key is None or png is None:
                    handled(rec)  # PDF unreadable; failure already recorded
                    continue
                cached = rec.cache.get(key)
                if cached is not None:
                    try:
                        check_answer(cached, cfg.parse.ocr_reject_scripts)
                    except OcrError:
                        cached = None  # a rejected answer is asked for again
                if cached is not None:
                    rec.results[page] = cached
                    stats.cached += 1
                    handled(rec)
                    continue
                if not ocr:
                    handled(rec)  # stays pending
                    continue
                if client is None:
                    client = EndpointClient.from_config(
                        cfg, max_retries=cfg.parse.max_retries
                    )
                fut = pool.submit(
                    ocr_page,
                    client,
                    model=model,
                    prompt=prompt,
                    png=png,
                    max_tokens=cfg.parse.ocr_max_tokens,
                    retry_repetition_penalty=cfg.parse.ocr_retry_repetition_penalty,
                    reject_scripts=cfg.parse.ocr_reject_scripts,
                )
                stats.requests += 1
                in_flight[fut] = (rec, page, key)
            if not in_flight:
                break
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in finished:
                rec, page, key = in_flight.pop(fut)
                try:
                    text = fut.result()
                except OcrError as exc:
                    rec.failures[page] = str(exc)
                    stats.failed += 1
                    streak = streak + 1 if isinstance(exc, EndpointDown) else 0
                    if (
                        streak >= cfg.parse.max_consecutive_failures
                        and not stats.stopped
                    ):
                        stats.stopped = True
                        log.error(
                            "OCR stopped after %d failures in a row (last: %s); "
                            "the remaining pages stay pending",
                            streak,
                            exc,
                        )
                else:
                    streak = 0
                    rec.results[page] = text
                    rec.cache.put(key, text)
                handled(rec)
    finally:
        pool.shutdown(wait=True)
        jobs.close()
        if own and client is not None:
            client.close()
    # Records cut short by a stop keep their unanswered pages pending.
    for rec in records:
        if not rec.finished and (rec.handled or rec.results):
            _finish_ocr_record(con, cfg, rec, version)
    return stats


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def _remove_orphans(con: duckdb.DuckDBPyConnection) -> int:
    """Drop pages and state of records whose PDF is no longer downloaded."""
    keep = "SELECT record_id FROM downloads WHERE status = 'downloaded'"
    (n,) = con.execute(
        "SELECT count(DISTINCT record_id) FROM ("
        f"SELECT record_id FROM pages UNION ALL SELECT record_id FROM {STATE_TABLE}"
        f") WHERE record_id NOT IN ({keep})"
    ).fetchone()
    con.execute(f"DELETE FROM pages WHERE record_id NOT IN ({keep})")
    con.execute(f"DELETE FROM {STATE_TABLE} WHERE record_id NOT IN ({keep})")
    return int(n)


def _summarise(con: duckdb.DuckDBPyConnection, **run: Any) -> ParseSummary:
    """Counts over the whole ``pages`` table and ``parse_state``."""
    (pages,) = con.execute("SELECT count(*) FROM pages").fetchone()
    t, o, g, b, pending = con.execute(
        "SELECT coalesce(sum(n_text), 0), coalesce(sum(n_ocr), 0), "
        "coalesce(sum(n_garbled), 0), coalesce(sum(n_blank), 0), "
        f"coalesce(sum(len(ocr_pending)), 0) FROM {STATE_TABLE}"
    ).fetchone()
    by_status = dict(
        con.execute(
            f"SELECT status, count(*) FROM {STATE_TABLE} GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    )
    (ocr_pages,) = con.execute("SELECT count(*) FROM pages WHERE ocr").fetchone()
    by_language = dict(
        con.execute(
            "SELECT coalesce(language, 'none'), count(*) FROM pages "
            "GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    )
    cross: dict[str, dict[str, int]] = {}
    for boris, detected, n in con.execute(
        f"""
        WITH l AS (
          SELECT record_id, language, sum(length(text)) AS n FROM pages
          WHERE language IS NOT NULL GROUP BY ALL),
        d AS (SELECT record_id, arg_max(language, n) AS lang FROM l GROUP BY 1)
        SELECT coalesce(r.languages[1], 'none'), coalesce(d.lang, 'none'), count(*)
        FROM {STATE_TABLE} s
        LEFT JOIN records r USING (record_id)
        LEFT JOIN d USING (record_id)
        GROUP BY ALL ORDER BY 1, 3 DESC
        """
    ).fetchall():
        cross.setdefault(boris, {})[detected] = n
    return ParseSummary(
        code_version=code_version(),
        parser=parser_name(),
        parser_version=PARSER_VERSION,
        run_at=now(),
        pages=pages,
        by_class={"text": t, "ocr": o, "garbled": g, "blank": b},
        by_status=by_status,
        ocr_pages=ocr_pages,
        ocr_pending=pending,
        by_language=by_language,
        boris_vs_detected=cross,
        **run,
    )


def parse_pdfs(
    cfg: Config,
    *,
    force: bool = False,
    limit: int | None = None,
    record_ids: list[str] | None = None,
    ocr: bool = True,
    client: EndpointClient | None = None,
    progress: ProgressFn | None = None,
) -> ParseSummary:
    """Parse every downloaded PDF into ``pages``, with OCR for scanned pages.

    Args:
        cfg: The full config (``data``, ``parse``, ``models``, ``llm``, ``prompts``).
        force: Read every PDF again, even unchanged ones (OCR answers are still
            taken from the cache).
        limit: Read at most this many PDFs; the OCR pass then covers only them.
        record_ids: Only these records (for trial runs and debugging).
        ocr: Send pending pages to the vision model. If False, pending pages are
            filled from the cache only and the rest stay pending.
        client: Endpoint client to use instead of a new one (for tests).
        progress: Called as ``progress(pass_name, done, total)``.

    Returns:
        The summary, also written to ``<data.text_dir>/parse_summary.json``.

    Raises:
        ParseError: No PDFs have been downloaded yet, or the OCR prompt is missing.
    """
    version = code_version()
    settings = _settings_json(cfg)
    con = catalogue.connect(cfg.data.catalogue)
    try:
        catalogue.ensure_tables(con, ["records", "downloads", "pages"])
        con.execute(_STATE_SQL)
        downloaded = dict(
            con.execute(
                "SELECT record_id, sha256 FROM downloads "
                "WHERE status = 'downloaded' ORDER BY record_id"
            ).fetchall()
        )
        if not downloaded:
            raise ParseError(
                f"no downloaded PDFs in {cfg.data.catalogue}; "
                "run `chatcde harvest files` first"
            )
        removed = _remove_orphans(con)
        if removed:
            log.info("Removed pages of %d records no longer downloaded", removed)

        wanted = list(downloaded)
        if record_ids:
            unknown = sorted(set(record_ids) - set(downloaded))
            if unknown:
                log.warning("Not downloaded, skipped: %s", ", ".join(unknown))
            wanted = [r for r in wanted if r in set(record_ids)]

        state = {
            r[0]: r[1:]
            for r in con.execute(
                f"SELECT record_id, sha256, parser_version, settings, status, n_pages "
                f"FROM {STATE_TABLE}"
            ).fetchall()
        }
        stored = dict(
            con.execute(
                "SELECT record_id, count(*) FROM pages GROUP BY record_id"
            ).fetchall()
        )

        def finished(rid: str) -> bool:
            st = state.get(rid)
            return (
                st is not None
                and st[0] == downloaded[rid]
                and st[1] == PARSER_VERSION
                and st[2] == settings
                and st[3] in (DONE, NEEDS_OCR)
                and stored.get(rid, 0) == st[4]
            )

        todo = [r for r in wanted if force or not finished(r)]
        skipped = len(wanted) - len(todo)
        if limit is not None:
            todo = todo[:limit]
        log.info(
            "%d records with a PDF: %d parsed earlier, reading %d%s",
            len(wanted),
            skipped,
            len(todo),
            " (--force)" if force else "",
        )

        errors = 0
        for i, rid in enumerate(todo, 1):
            try:
                result, error = read_record(cfg, rid, version), None
            except (OSError, ValueError, RuntimeError) as exc:
                result, error = None, f"{type(exc).__name__}: {exc}"
                errors += 1
                log.warning("%s: %s", rid, error)
            _store_pass1(con, rid, downloaded[rid], settings, version, result, error)
            if result is not None:
                log.debug(
                    "%s: %d pages %s", rid, len(result.pages), dict(result.counts)
                )
            if progress:
                progress("text", i, len(todo))
            if i % 100 == 0:
                log.info("%d/%d PDFs read", i, len(todo))

        ocr_scope = todo if limit is not None else wanted
        stats = run_ocr(
            con,
            cfg,
            ocr_scope,
            ocr=ocr,
            client=client,
            version=version,
            progress=progress,
        )
        summary = _summarise(
            con,
            records=len(downloaded),
            parsed=len(todo),
            skipped=skipped,
            removed=removed,
            errors=errors,
            ocr_requests=stats.requests,
            ocr_cached=stats.cached,
            ocr_failed=stats.failed,
            ocr_stopped=stats.stopped,
        )
    finally:
        con.close()

    write_json(
        cfg.data.text_dir / "parse_summary.json", summary.model_dump(mode="json")
    )
    log.info(
        "Pages: %d (%s); OCR: %d done, %d pending; records by status %s",
        summary.pages,
        summary.by_class,
        summary.ocr_pages,
        summary.ocr_pending,
        summary.by_status,
    )
    return summary
