"""``chatcde index embed``: embed chunks and search them by vector similarity.

Vectors live in an ``embeddings`` table in the catalogue next to ``chunks``,
one row per chunk *and model*, so the first-choice and the alternative embedding
model can be compared on the same chunks. Search is an exact (brute-force)
cosine scan in DuckDB, which is fast enough for the ~100k chunks of CDE's
corpus and keeps the index inside the one catalogue file that data bundles ship.
See docs/decisions/0012-embeddings.md.

* Document input: ``title + blank line + chunk text`` (``embedding.include_title``),
  so a chunk from the middle of a paper still says what the paper is about.
* Query input: the model's instruction prefix from
  ``embedding.query_instructions`` (Qwen3-Embedding is trained with one).
* Vectors are truncated to ``embedding.dimensions`` (Matryoshka models such as
  qwen3-embedding-4b keep most quality at 1,024 of 2,560 dimensions) and
  L2-normalised, so the dot product is the cosine similarity.
* Resumable and idempotent: each row stores a fingerprint of its input text,
  model and dimensions; a run embeds only chunks that are new or changed, and
  removes rows of chunks that no longer exist. ``force`` re-embeds every chunk
  for the chosen model.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
from pydantic import BaseModel, Field

from chatcde import catalogue
from chatcde.harvest.common import code_version, now
from chatcde.store.endpoint import EndpointClient

log = logging.getLogger(__name__)

#: Track B table with one vector per (chunk, model); not a shared schema table.
TABLE = "embeddings"
_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  chunk_id VARCHAR NOT NULL,
  record_id VARCHAR NOT NULL,
  model VARCHAR NOT NULL,
  dims INTEGER NOT NULL,
  fingerprint VARCHAR NOT NULL,
  vector FLOAT[] NOT NULL,
  code_version VARCHAR NOT NULL,
  embedded_at TIMESTAMP NOT NULL
)
"""


class EmbeddingError(Exception):
    """The catalogue is not ready for embedding, or the endpoint failed."""


class EmbedSettings(BaseModel):
    """Settings of one embedding run (``models`` and ``embedding`` in the config)."""

    model: str
    batch_size: int = Field(ge=1)
    dimensions: int | None = Field(default=None, ge=1)
    include_title: bool = True
    max_concurrency: int = Field(default=1, ge=1)


class EmbedSummary(BaseModel):
    """What one ``chatcde index embed`` run did (printed and logged)."""

    code_version: str
    model: str
    dims: int | None
    built_at: datetime
    chunks: int
    embedded: int
    skipped: int
    removed: int
    remaining: int
    requests: int
    seconds: float
    vectors_total: int


@dataclass(frozen=True)
class _Item:
    """One chunk to embed: its IDs, the input text and that text's fingerprint."""

    chunk_id: str
    record_id: str
    text: str
    fingerprint: str


def document_input(title: str, text: str, include_title: bool) -> str:
    """Text sent to the embedding model for one chunk."""
    return f"{title}\n\n{text}" if include_title and title else text


def query_input(query: str, instruction: str | None) -> str:
    """Text sent to the embedding model for a search query.

    Uses the Qwen3-Embedding format (``Instruct: <instruction>``, a line break,
    ``Query: <query>``) when the model has an instruction in the config,
    otherwise the bare query.
    """
    return f"Instruct: {instruction}\nQuery: {query}" if instruction else query


def prepare_vector(vector: Sequence[float], dimensions: int | None) -> list[float]:
    """Truncate to ``dimensions`` (if smaller than the vector) and L2-normalise.

    Raises:
        EmbeddingError: If the vector is all zeros.
    """
    v = list(vector[:dimensions] if dimensions else vector)
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        raise EmbeddingError("the model returned a zero vector")
    return [x / norm for x in v]


def fingerprint(text: str, model: str, dimensions: int | None) -> str:
    """Hash of everything that determines a stored vector."""
    data = f"{model}\x00{dimensions}\x00{text}".encode()
    return hashlib.sha256(data).hexdigest()


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """True if the catalogue has a table called ``name``."""
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    row = con.execute(sql, [name]).fetchone()
    return bool(row and row[0])


def _load_items(con: duckdb.DuckDBPyConnection, settings: EmbedSettings) -> list[_Item]:
    """All chunks with the text to embed, in chunk order."""
    rows = con.execute(
        "SELECT c.chunk_id, c.record_id, c.text, coalesce(r.title, '') "
        "FROM chunks c LEFT JOIN records r USING (record_id) ORDER BY c.chunk_id"
    ).fetchall()
    items = []
    for chunk_id, record_id, text, title in rows:
        doc = document_input(title, text, settings.include_title)
        fp = fingerprint(doc, settings.model, settings.dimensions)
        items.append(_Item(chunk_id, record_id, doc, fp))
    return items


def build_embeddings(
    db_path: Path,
    client: EndpointClient,
    settings: EmbedSettings,
    *,
    force: bool = False,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> EmbedSummary:
    """Embed every chunk that has no current vector for ``settings.model``.

    Requests go out in batches of ``batch_size`` chunks, up to
    ``max_concurrency`` at a time. Each batch is written in its own transaction
    as soon as it returns, so an interrupted run keeps what it finished.

    Args:
        db_path: The catalogue DuckDB file.
        client: Endpoint client (see :class:`EndpointClient.from_config`).
        settings: Model and batch settings.
        force: Embed every chunk again for this model.
        limit: Embed at most this many chunks in this run (for trying out).
        progress: Called as ``progress(done, total)`` after each batch.

    Returns:
        A summary of the run.

    Raises:
        EmbeddingError: If the catalogue or its chunks are missing.
        chatcde.store.endpoint.EndpointError: If the endpoint keeps failing;
            batches written before the failure are kept.
    """
    if not db_path.is_file():
        raise EmbeddingError(f"no catalogue at {db_path}")
    started = time.monotonic()
    version = code_version()
    con = catalogue.connect(db_path)
    try:
        if not _has_table(con, "chunks"):
            raise EmbeddingError(
                "the catalogue has no chunks; run `chatcde index chunk` first"
            )
        con.execute(_TABLE_SQL)
        items = _load_items(con, settings)
        removed = _remove_stale(
            con, settings.model, {i.chunk_id: i.fingerprint for i in items}
        )

        current = {
            cid
            for (cid,) in con.execute(
                f"SELECT chunk_id FROM {TABLE} WHERE model = ?", [settings.model]
            ).fetchall()
        }
        todo = items if force else [i for i in items if i.chunk_id not in current]
        skipped = len(items) - len(todo)
        remaining = 0
        if limit is not None and len(todo) > limit:
            remaining = len(todo) - limit
            todo = todo[:limit]
        log.info(
            "embedding %d of %d chunks with %s (%d up to date%s)",
            len(todo),
            len(items),
            settings.model,
            skipped,
            ", --force" if force else "",
        )

        batches = [
            todo[i : i + settings.batch_size]
            for i in range(0, len(todo), settings.batch_size)
        ]
        dims: int | None = None
        done = 0
        with ThreadPoolExecutor(max_workers=settings.max_concurrency) as pool:
            # Submit a bounded window of batches so memory stays flat.
            window = settings.max_concurrency * 2
            for start in range(0, len(batches), window):
                part = batches[start : start + window]
                results = pool.map(
                    lambda b: client.embed(settings.model, [i.text for i in b]), part
                )
                for batch, raw in zip(part, results, strict=True):
                    vectors = [prepare_vector(v, settings.dimensions) for v in raw]
                    dims = len(vectors[0])
                    _write_batch(con, settings.model, batch, vectors, version)
                    done += len(batch)
                    if progress:
                        progress(done, len(todo))

        row = con.execute(
            f"SELECT count(*), max(dims) FROM {TABLE} WHERE model = ?", [settings.model]
        ).fetchone()
        total = int(row[0]) if row else 0
        if dims is None and row and row[1] is not None:
            dims = int(row[1])
    finally:
        con.close()

    summary = EmbedSummary(
        code_version=version,
        model=settings.model,
        dims=dims,
        built_at=now(),
        chunks=len(items),
        embedded=done,
        skipped=skipped,
        removed=removed,
        remaining=remaining,
        requests=len(batches),
        seconds=round(time.monotonic() - started, 1),
        vectors_total=total,
    )
    log.info(
        "embedded %d chunks with %s in %d requests (%.1f s); %d skipped, %d removed, "
        "%d remaining; %d vectors for this model",
        summary.embedded,
        summary.model,
        summary.requests,
        summary.seconds,
        summary.skipped,
        summary.removed,
        summary.remaining,
        summary.vectors_total,
    )
    return summary


def _write_batch(
    con: duckdb.DuckDBPyConnection,
    model: str,
    batch: Sequence[_Item],
    vectors: Sequence[list[float]],
    version: str,
) -> None:
    """Replace the rows of one batch's chunks for ``model`` in one transaction."""
    stamp = now().replace(tzinfo=None)
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            f"DELETE FROM {TABLE} WHERE model = ? AND chunk_id IN (SELECT unnest(?))",
            [model, [i.chunk_id for i in batch]],
        )
        con.executemany(
            f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    i.chunk_id,
                    i.record_id,
                    model,
                    len(v),
                    i.fingerprint,
                    v,
                    version,
                    stamp,
                ]
                for i, v in zip(batch, vectors, strict=True)
            ],
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _remove_stale(
    con: duckdb.DuckDBPyConnection, model: str, wanted: dict[str, str]
) -> int:
    """Delete ``model``'s rows whose chunk is gone or whose input changed.

    Args:
        con: Open catalogue.
        model: Only this model's rows are touched.
        wanted: chunk_id -> fingerprint of the input it should have now.

    Returns:
        The number of rows removed.
    """
    rows = con.execute(
        f"SELECT chunk_id, fingerprint FROM {TABLE} WHERE model = ?", [model]
    ).fetchall()
    stale = [cid for cid, fp in rows if wanted.get(cid) != fp]
    if stale:
        con.execute(
            f"DELETE FROM {TABLE} WHERE model = ? AND chunk_id IN (SELECT unnest(?))",
            [model, stale],
        )
        log.info("removed %d vectors of changed or deleted chunks", len(stale))
    return len(stale)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class VectorHit(BaseModel):
    """One vector-search result: a chunk and its cosine similarity to the query."""

    chunk_id: str
    record_id: str
    score: float


def embed_query(
    client: EndpointClient,
    query: str,
    *,
    model: str,
    dimensions: int | None,
    instruction: str | None,
) -> list[float]:
    """Embed a search query the same way the chunks were embedded."""
    (raw,) = client.embed(model, [query_input(query, instruction)])
    return prepare_vector(raw, dimensions)


def vector_search(
    con: duckdb.DuckDBPyConnection,
    query_vector: Sequence[float],
    *,
    model: str,
    k: int,
    record_ids: Sequence[str] | None = None,
) -> list[VectorHit]:
    """Return the ``k`` chunks most similar to ``query_vector``.

    An exact scan: every stored vector of ``model`` is compared with the query.
    Both sides are L2-normalised, so the dot product is the cosine similarity.

    Args:
        con: Open catalogue (read-only is enough).
        query_vector: Output of :func:`embed_query` for the same model and dimensions.
        model: Embedding model whose vectors to search.
        k: Number of hits.
        record_ids: If given, search only chunks of these records (how
            :class:`~chatcde.schemas.Filters` are applied, issue #22).

    Returns:
        Hits, best first.
    """
    params: list[object] = [list(query_vector), model, len(query_vector)]
    where = "model = ? AND dims = ?"
    if record_ids is not None:
        where += " AND record_id IN (SELECT unnest(?))"
        params.append(list(record_ids))
    params.append(k)
    rows = con.execute(
        f"SELECT chunk_id, record_id, list_dot_product(vector, ?::FLOAT[]) AS score "
        f"FROM {TABLE} WHERE {where} ORDER BY score DESC, chunk_id LIMIT ?",
        params,
    ).fetchall()
    return [VectorHit(chunk_id=c, record_id=r, score=float(s)) for c, r, s in rows]
