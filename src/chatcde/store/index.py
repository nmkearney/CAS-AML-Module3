"""``chatcde index chunk``: build the ``chunks`` table from records and pages.

Reads Track A's ``records`` and ``pages`` tables, splits each record with
:func:`chatcde.store.chunking.chunk_record` and writes :class:`~chatcde.schemas.Chunk`
rows to the ``chunks`` table of the same catalogue. See
docs/decisions/0011-chunking.md.

Resumable and idempotent: a Track-B side table ``chunk_state`` keeps, per
record, a fingerprint of its input text and the chunk settings, plus the code
version and time of the run. A record is chunked again only when its
fingerprint changed (new text, new settings, new chunker version), when its
chunks went missing, or with ``force``. Each record is replaced in its own
transaction, so an interrupted run loses at most one record's work. Records no
longer in the catalogue lose their chunks.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from chatcde import catalogue
from chatcde.harvest.common import code_version, now
from chatcde.schemas import PageText, Record
from chatcde.store.chunking import CHUNKER_VERSION, ChunkSettings, chunk_record

log = logging.getLogger(__name__)

#: Per-record state of the chunking stage (owned by Track B, not a shared table).
STATE_TABLE = "chunk_state"
_STATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
  record_id VARCHAR NOT NULL,
  fingerprint VARCHAR NOT NULL,
  n_chunks BIGINT NOT NULL,
  chunker_version VARCHAR NOT NULL,
  code_version VARCHAR NOT NULL,
  settings VARCHAR NOT NULL,
  chunked_at TIMESTAMP NOT NULL
)
"""


class ChunkingError(Exception):
    """The catalogue is missing or not ready for indexing."""


class ChunkSummary(BaseModel):
    """What one ``chatcde index chunk`` run did (printed and logged)."""

    code_version: str
    chunker_version: str
    settings: ChunkSettings
    built_at: datetime
    records: int
    chunked: int
    skipped: int
    removed: int
    remaining: int
    chunks_written: int
    chunks_total: int
    by_source: dict[str, int]
    max_tokens: int
    mean_tokens: float


def fingerprint(record: Record, pages: list[PageText], settings: ChunkSettings) -> str:
    """Hash of everything that determines a record's chunks.

    Includes the fields :func:`chunk_record` reads, the page texts, the chunk
    settings and :data:`CHUNKER_VERSION`, but not the code version: an unrelated
    commit should not force a full re-chunk.
    """
    payload = {
        "chunker": CHUNKER_VERSION,
        "settings": settings.model_dump(),
        "title": record.title,
        "abstract": record.abstract,
        "abstract_source": record.abstract_source,
        "keywords": record.keywords,
        "container": record.container,
        "languages": record.languages,
        "pages": [
            (p.page, p.text, p.language) for p in sorted(pages, key=lambda p: p.page)
        ],
    }
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _dicts(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    """Run ``sql`` and return the rows as dicts keyed by column name."""
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """True if the catalogue has a table called ``name``."""
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    row = con.execute(sql, [name]).fetchone()
    return bool(row and row[0])


def load_inputs(
    con: duckdb.DuckDBPyConnection,
) -> tuple[list[Record], dict[str, list[PageText]]]:
    """Read and validate all records and their pages from the catalogue.

    Raises:
        ChunkingError: If there is no ``records`` table yet.
    """
    if not _has_table(con, "records"):
        raise ChunkingError(
            "the catalogue has no records table; run `chatcde harvest metadata` "
            "or `chatcde fetch-data` first"
        )
    records = [
        Record.model_validate(r)
        for r in _dicts(con, "SELECT * FROM records ORDER BY record_id")
    ]
    pages: dict[str, list[PageText]] = defaultdict(list)
    if _has_table(con, "pages"):
        for row in _dicts(con, "SELECT * FROM pages ORDER BY record_id, page"):
            p = PageText.model_validate(row)
            pages[p.record_id].append(p)
    else:
        log.warning(
            "the catalogue has no pages table; chunking abstracts and metadata only"
        )
    return records, pages


def build_chunks(
    db_path: Path,
    settings: ChunkSettings,
    *,
    force: bool = False,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ChunkSummary:
    """Chunk every record in the catalogue that needs it.

    Args:
        db_path: The catalogue DuckDB file (``data.catalogue`` in the config).
        settings: Chunk size settings (``retrieval`` section of the config).
        force: Chunk every record again, even if nothing changed.
        limit: Chunk at most this many records in this run (for trying out);
            the rest are counted as ``remaining``.
        progress: Called as ``progress(done, total)`` after each chunked record.

    Returns:
        A summary of the run.

    Raises:
        ChunkingError: If the catalogue file or its records table is missing.
    """
    if not db_path.is_file():
        raise ChunkingError(
            f"no catalogue at {db_path}; run the harvest or fetch-data first"
        )
    version = code_version()
    settings_json = settings.model_dump_json()
    con = catalogue.connect(db_path)
    try:
        records, pages = load_inputs(con)
        catalogue.ensure_tables(con, ["chunks"])
        con.execute(_STATE_SQL)

        removed = _remove_orphans(con)
        state = dict(
            con.execute(f"SELECT record_id, fingerprint FROM {STATE_TABLE}").fetchall()
        )
        stored = dict(
            con.execute(
                "SELECT record_id, count(*) FROM chunks GROUP BY record_id"
            ).fetchall()
        )
        expected = dict(
            con.execute(f"SELECT record_id, n_chunks FROM {STATE_TABLE}").fetchall()
        )

        todo: list[tuple[Record, str]] = []
        skipped = 0
        for rec in records:
            fp = fingerprint(rec, pages.get(rec.record_id, []), settings)
            up_to_date = state.get(rec.record_id) == fp and stored.get(
                rec.record_id, 0
            ) == expected.get(rec.record_id)
            if up_to_date and not force:
                skipped += 1
            else:
                todo.append((rec, fp))
        remaining = 0
        if limit is not None and len(todo) > limit:
            remaining = len(todo) - limit
            todo = todo[:limit]
        log.info(
            "chunking %d of %d records (%d up to date%s)",
            len(todo),
            len(records),
            skipped,
            ", --force" if force else "",
        )

        by_source: Counter[str] = Counter()
        tokens: list[int] = []
        for done, (rec, fp) in enumerate(todo, 1):
            chunks = chunk_record(rec, pages.get(rec.record_id, []), settings)
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute("DELETE FROM chunks WHERE record_id = ?", [rec.record_id])
                con.execute(
                    f"DELETE FROM {STATE_TABLE} WHERE record_id = ?", [rec.record_id]
                )
                catalogue.insert(con, "chunks", chunks)
                con.execute(
                    f"INSERT INTO {STATE_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        rec.record_id,
                        fp,
                        len(chunks),
                        CHUNKER_VERSION,
                        version,
                        settings_json,
                        now().replace(tzinfo=None),
                    ],
                )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            by_source.update(c.source.value for c in chunks)
            tokens.extend(c.token_count for c in chunks)
            log.debug("%s: %d chunks", rec.record_id, len(chunks))
            if progress:
                progress(done, len(todo))

        row = con.execute("SELECT count(*) FROM chunks").fetchone()
        total = int(row[0]) if row else 0
    finally:
        con.close()

    summary = ChunkSummary(
        code_version=version,
        chunker_version=CHUNKER_VERSION,
        settings=settings,
        built_at=now(),
        records=len(records),
        chunked=len(todo),
        skipped=skipped,
        removed=removed,
        remaining=remaining,
        chunks_written=len(tokens),
        chunks_total=total,
        by_source=dict(by_source.most_common()),
        max_tokens=max(tokens, default=0),
        mean_tokens=round(sum(tokens) / len(tokens), 1) if tokens else 0.0,
    )
    log.info(
        "chunked %d records into %d chunks (%d skipped, %d removed, %d remaining); "
        "%d chunks in the catalogue",
        summary.chunked,
        summary.chunks_written,
        summary.skipped,
        summary.removed,
        summary.remaining,
        summary.chunks_total,
    )
    return summary


def _remove_orphans(con: duckdb.DuckDBPyConnection) -> int:
    """Delete chunks and state of records that are no longer in ``records``.

    Returns:
        The number of records whose chunks were removed.
    """
    orphan_sql = (
        "SELECT DISTINCT record_id FROM ({}) "
        "WHERE record_id NOT IN (SELECT record_id FROM records)"
    ).format(f"SELECT record_id FROM chunks UNION SELECT record_id FROM {STATE_TABLE}")
    orphans = [r[0] for r in con.execute(orphan_sql).fetchall()]
    if orphans:
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                "DELETE FROM chunks WHERE record_id IN (SELECT unnest(?))", [orphans]
            )
            con.execute(
                f"DELETE FROM {STATE_TABLE} WHERE record_id IN (SELECT unnest(?))",
                [orphans],
            )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        log.info(
            "removed chunks of %d records no longer in the catalogue", len(orphans)
        )
    return len(orphans)
