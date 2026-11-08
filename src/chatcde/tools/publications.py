"""The search and reading tools behind ``chatcde.tools.core``.

Issue #23; see docs/decisions/0020-search-and-reading-tools.md. Each function
takes the :class:`~chatcde.tools.context.ToolContext` explicitly; the public
versions in :mod:`chatcde.tools.core` pass the active one, so the signatures the
agent sees stay as agreed.

* ``search_publications`` wraps :func:`chatcde.store.search.hybrid_search` and
  turns each chunk into a :class:`~chatcde.schemas.Passage` with a citation.
* ``get_publication`` accepts a BORIS UUID, a DOI (publisher's or BORIS's own,
  bare or as a URL) or a BORIS handle.
* ``read_section`` returns the full-text chunks that overlap a page range,
  verbatim (chunk text is never prefixed with the title, decision 0011).

Problems the caller can fix (unknown ID, no full text, page range too wide)
raise :class:`~chatcde.tools.context.ToolError` with a message that says what to
do instead.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import duckdb

from chatcde.schemas import (
    Author,
    Chunk,
    Citation,
    Filters,
    ManifestEntry,
    Passage,
    PublicationDetail,
    Record,
    Tag,
    TextSource,
)
from chatcde.store.endpoint import EndpointError
from chatcde.store.filters import FilterError
from chatcde.store.search import SearchError, SearchSettings, hybrid_search
from chatcde.tools.context import ToolContext, ToolError

log = logging.getLogger(__name__)

_DOI_RE = re.compile(r"(10\.\d{4,9}/\S+)", re.IGNORECASE)
_HANDLE_RE = re.compile(r"(\d+(?:\.\d+)*/\d+)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dicts(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> list[dict[str, Any]]:
    """Run ``sql`` and return the rows as dicts keyed by column name."""
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """True if the catalogue has a table called ``name``."""
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    row = con.execute(sql, [name]).fetchone()
    return bool(row and row[0])


def normalise_doi(text: str) -> str | None:
    """Extract a bare, lower-case DOI from ``text`` (``doi:``, URLs and all).

    Returns:
        ``10.xxxx/...`` or ``None`` if ``text`` holds no DOI.
    """
    m = _DOI_RE.search(text.strip())
    return m.group(1).rstrip(".,;").lower() if m else None


def citation(record: Record, page: int | None = None) -> Citation:
    """Build the citation for a record (and optionally a page).

    The DOI is the publisher's when there is one, since that is the one readers
    cite; otherwise BORIS's own DataCite DOI (decision 0010).
    """
    return Citation(
        record_id=record.record_id,
        title=record.title,
        year=record.year,
        doi=record.publisher_doi or record.doi,
        boris_url=record.boris_url,
        page=page,
    )


def resolve_record(con: duckdb.DuckDBPyConnection, ref: str) -> Record:
    """Find a record by BORIS UUID, DOI (publisher's or BORIS's) or handle.

    Raises:
        ToolError: If no record matches.
    """
    ref = ref.strip()
    if not ref:
        raise ToolError("empty record ID; pass a BORIS ID or DOI")
    if not _has_table(con, "records"):
        raise ToolError("the catalogue has no records; run `chatcde fetch-data`")
    rows = _dicts(con, "SELECT * FROM records WHERE record_id = ?", [ref])
    if not rows and (doi := normalise_doi(ref)):
        rows = _dicts(
            con,
            "SELECT * FROM records WHERE lower(publisher_doi) = ? OR lower(doi) = ? "
            "ORDER BY record_id",
            [doi, doi],
        )
    if not rows and (m := _HANDLE_RE.search(ref)):
        rows = _dicts(
            con,
            "SELECT * FROM records WHERE handle = ? ORDER BY record_id",
            [m.group(1)],
        )
    if not rows:
        raise ToolError(
            f"no CDE publication with ID, DOI or handle {ref!r}; "
            "use search_publications to find one"
        )
    if len(rows) > 1:
        log.warning(
            "%r matches %d records; using %s", ref, len(rows), rows[0]["record_id"]
        )
    return Record.model_validate(rows[0])


def _records_by_id(
    con: duckdb.DuckDBPyConnection, record_ids: list[str]
) -> dict[str, Record]:
    """Load records by ID."""
    rows = _dicts(
        con,
        "SELECT * FROM records WHERE record_id IN (SELECT unnest(?::VARCHAR[]))",
        [record_ids],
    )
    return {r["record_id"]: Record.model_validate(r) for r in rows}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def search_publications(
    ctx: ToolContext, query: str, filters: Filters | None = None, k: int = 8
) -> list[Passage]:
    """Hybrid search; see :func:`chatcde.tools.core.search_publications`.

    ``k`` is capped at ``tools.max_k``; the candidates before reranking are at
    least ``retrieval.top_k`` and never fewer than ``k``.

    Raises:
        ToolError: Empty query, ``k < 1``, no index, tag filters without tags,
            or the endpoint failing.
    """
    if not query.strip():
        raise ToolError("empty query; say what to search for")
    if k < 1:
        raise ToolError("k must be at least 1")
    cfg = ctx.cfg
    if k > cfg.tools.max_k:
        log.info("k=%d capped at tools.max_k=%d", k, cfg.tools.max_k)
        k = cfg.tools.max_k
    settings = SearchSettings.from_config(
        cfg, top_n=k, candidates=max(cfg.retrieval.top_k, k)
    )
    client = ctx.endpoint() if settings.needs_client else None
    with ctx.catalogue() as con:
        try:
            hits = hybrid_search(con, client, query, settings, filters)
        except (SearchError, FilterError) as exc:
            raise ToolError(str(exc)) from exc
        except EndpointError as exc:
            raise ToolError(f"the search model endpoint failed: {exc}") from exc
        records = _records_by_id(con, sorted({h.chunk.record_id for h in hits}))
    passages = []
    for h in hits:
        rec = records.get(h.chunk.record_id)
        if rec is None:  # chunk of a record removed since indexing
            continue
        passages.append(
            Passage(
                chunk=h.chunk, score=h.score, citation=citation(rec, h.chunk.page_start)
            )
        )
    log.info("search_publications %r: %d passages", query, len(passages))
    return passages


def get_publication(ctx: ToolContext, record_id: str) -> PublicationDetail:
    """Everything about one record; see :func:`chatcde.tools.core.get_publication`.

    Tags are sorted by dimension, then by confidence (highest first).

    Raises:
        ToolError: If no record matches ``record_id``.
    """
    with ctx.catalogue() as con:
        record = resolve_record(con, record_id)
        rid = record.record_id
        authors = (
            [
                Author.model_validate(r)
                for r in _dicts(
                    con,
                    "SELECT * FROM authors WHERE record_id = ? ORDER BY position",
                    [rid],
                )
            ]
            if _has_table(con, "authors")
            else []
        )
        tags = (
            [
                Tag.model_validate(r)
                for r in _dicts(
                    con,
                    "SELECT * FROM tags WHERE record_id = ? "
                    "ORDER BY dimension, confidence DESC, term",
                    [rid],
                )
            ]
            if _has_table(con, "tags")
            else []
        )
        download = None
        if _has_table(con, "downloads"):
            rows = _dicts(
                con,
                "SELECT * FROM downloads WHERE record_id = ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                [rid],
            )
            download = ManifestEntry.model_validate(rows[0]) if rows else None
    return PublicationDetail(
        record=record, authors=authors, tags=tags, download=download
    )


def read_section(
    ctx: ToolContext, record_id: str, page_start: int, page_end: int | None = None
) -> list[Chunk]:
    """Full-text chunks of a page range; see :func:`chatcde.tools.core.read_section`.

    Returns every full-text chunk whose pages overlap ``page_start..page_end``,
    in reading order. Neighbouring chunks share ``retrieval.chunk_overlap_tokens``
    of text, so a quote may appear at the end of one and the start of the next.

    Raises:
        ToolError: Bad or too wide page range (more than
            ``tools.max_section_pages``), unknown record, no full text, or no
            text on those pages (the message gives the pages that have text).
    """
    end = page_start if page_end is None else page_end
    if page_start < 1 or end < page_start:
        raise ToolError(f"bad page range {page_start}-{end}; pages start at 1")
    limit = ctx.cfg.tools.max_section_pages
    if end - page_start + 1 > limit:
        raise ToolError(
            f"page range {page_start}-{end} is too wide; read at most {limit} pages "
            "at a time"
        )
    with ctx.catalogue() as con:
        record = resolve_record(con, record_id)
        if not _has_table(con, "chunks"):
            raise ToolError("the catalogue has no chunks; run `chatcde index chunk`")
        rid = record.record_id
        rows = _dicts(
            con,
            "SELECT * FROM chunks WHERE record_id = ? AND source = ? "
            "AND page_start <= ? AND coalesce(page_end, page_start) >= ? "
            "ORDER BY page_start, "
            "CAST(regexp_extract(chunk_id, ':(\\d+)$', 1) AS INTEGER)",
            [rid, TextSource.FULLTEXT.value, end, page_start],
        )
        if rows:
            return [Chunk.model_validate(r) for r in rows]
        span = con.execute(
            "SELECT min(page_start), max(coalesce(page_end, page_start)) FROM chunks "
            "WHERE record_id = ? AND source = ?",
            [rid, TextSource.FULLTEXT.value],
        ).fetchone()
    if not span or span[0] is None:
        raise ToolError(
            f"no full text for {rid} (access: {record.access.value}); its abstract "
            "and metadata are available through get_publication"
        )
    raise ToolError(
        f"no text on pages {page_start}-{end} of {rid}; "
        f"its full text covers pages {span[0]}-{span[1]}"
    )
