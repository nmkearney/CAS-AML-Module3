"""``chatcde index bm25``: a keyword (BM25) index over the chunks, kept in DuckDB.

The keyword half of hybrid search (issue #22, docs/decisions/0013-hybrid-search.md).
Vectors find passages that *mean* the same as the query, also across languages;
BM25 finds passages that contain the query's exact *words*: place names,
acronyms, project and method names that embeddings blur.

* Storage: two Track B tables in the catalogue, next to ``chunks`` and
  ``embeddings``: ``bm25_docs`` (one row per chunk: length and fingerprint) and
  ``bm25_postings`` (one row per term and chunk: term frequency). Scoring is one
  SQL query, so no extension or extra file is needed and bundles ship the index
  inside the catalogue.
* Tokens: case-folded, accents removed, words split on non-word characters,
  common EN/DE/FR/ES stopwords and single Latin letters dropped. No stemming:
  inflections and other languages are left to the vectors.
* Indexed text: the same ``title + chunk text`` the embedding model sees
  (:func:`chatcde.store.embeddings.document_input`).
* Resumable and idempotent: each chunk's row stores a fingerprint of its input
  and the tokenizer version; a run indexes only new or changed chunks and drops
  rows of chunks that are gone. ``force`` rebuilds everything.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
from pydantic import BaseModel

from chatcde import catalogue
from chatcde.harvest.common import code_version, now
from chatcde.store.embeddings import document_input

log = logging.getLogger(__name__)

#: Bump when :func:`tokenize` changes; every chunk is then indexed again.
TOKENIZER_VERSION = "1"

DOCS_TABLE = "bm25_docs"
POSTINGS_TABLE = "bm25_postings"
_TABLES_SQL = [
    f"""
CREATE TABLE IF NOT EXISTS {DOCS_TABLE} (
  chunk_id VARCHAR NOT NULL,
  record_id VARCHAR NOT NULL,
  length INTEGER NOT NULL,
  fingerprint VARCHAR NOT NULL,
  code_version VARCHAR NOT NULL,
  indexed_at TIMESTAMP NOT NULL
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {POSTINGS_TABLE} (
  term VARCHAR NOT NULL,
  chunk_id VARCHAR NOT NULL,
  tf INTEGER NOT NULL
)
""",
]

#: Chunks written per transaction.
_BATCH = 500

_STOPWORDS_RAW = """
a an and are as at be been but by can could do does for from had has have he her
his how i if in into is it its may might more most no not of on or our she should
so such than that the their them then there these they this those to too us was
we were what when where which while who why will with would you your also between
both each other only same some about after before over under within without
aber als am an auch auf aus bei bis da das dass dem den der des die dies diese
dieser dieses doch du durch ein eine einem einen einer eines er es fur hat hatte
ich ihr im in ist ja kann man mit nach nicht noch nur ob oder sein sich sie sind
so uber um und uns von vor war was weil welche wenn werden wie wir wird wo
zu zum zur
au aux avec ce ces dans de des du elle en est et il ils la le les leur lui mais
nous ou par pas pour qu que qui sa se ses son sont sur un une vous
al como con del el en es esta este la las lo los mas para pero por que se sin
su sus una unas uno unos y
"""


def _fold(text: str) -> str:
    """Case-fold and strip accents (``Bödenerosión`` -> ``bodenerosion``)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


STOPWORDS: frozenset[str] = frozenset(_fold(w) for w in _STOPWORDS_RAW.split())
_WORD = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    """Split ``text`` into BM25 terms (used for chunks and queries alike).

    Folds case and accents, splits on non-word characters, and drops stopwords
    and single Latin letters. Digits are kept (years, SDG numbers).
    """
    return [
        w
        for w in _WORD.findall(_fold(text))
        if w not in STOPWORDS and not (len(w) == 1 and w.isascii() and w.isalpha())
    ]


def fingerprint(text: str) -> str:
    """Hash of everything that determines a chunk's postings."""
    data = f"{TOKENIZER_VERSION}\x00{text}".encode()
    return hashlib.sha256(data).hexdigest()


class Bm25Error(Exception):
    """The catalogue is not ready for the keyword index."""


class Bm25Summary(BaseModel):
    """What one ``chatcde index bm25`` run did (printed and logged)."""

    code_version: str
    tokenizer_version: str
    built_at: datetime
    chunks: int
    indexed: int
    skipped: int
    removed: int
    terms: int
    postings: int
    seconds: float


class Bm25Hit(BaseModel):
    """One keyword-search result: a chunk and its BM25 score."""

    chunk_id: str
    record_id: str
    score: float


@dataclass(frozen=True)
class _Item:
    """One chunk to index: its IDs, the text to index and that text's fingerprint."""

    chunk_id: str
    record_id: str
    text: str
    fingerprint: str


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """True if the catalogue has a table called ``name``."""
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    row = con.execute(sql, [name]).fetchone()
    return bool(row and row[0])


def has_index(con: duckdb.DuckDBPyConnection) -> bool:
    """True if the catalogue holds a non-empty BM25 index."""
    if not _has_table(con, DOCS_TABLE):
        return False
    row = con.execute(f"SELECT count(*) FROM {DOCS_TABLE}").fetchone()
    return bool(row and row[0])


def _load_items(con: duckdb.DuckDBPyConnection, include_title: bool) -> list[_Item]:
    """All chunks with the text to index, in chunk order."""
    rows = con.execute(
        "SELECT c.chunk_id, c.record_id, c.text, coalesce(r.title, '') "
        "FROM chunks c LEFT JOIN records r USING (record_id) ORDER BY c.chunk_id"
    ).fetchall()
    items = []
    for chunk_id, record_id, text, title in rows:
        doc = document_input(title, text, include_title)
        items.append(_Item(chunk_id, record_id, doc, fingerprint(doc)))
    return items


def build_bm25(
    db_path: Path,
    *,
    include_title: bool = True,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> Bm25Summary:
    """Bring the BM25 index up to date with the ``chunks`` table.

    Args:
        db_path: The catalogue DuckDB file.
        include_title: Index ``title + chunk text`` (``embedding.include_title``),
            as for the vectors.
        force: Index every chunk again.
        progress: Called as ``progress(done, total)`` after each batch.

    Returns:
        A summary of the run.

    Raises:
        Bm25Error: If the catalogue or its chunks are missing.
    """
    if not db_path.is_file():
        raise Bm25Error(f"no catalogue at {db_path}")
    started = time.monotonic()
    version = code_version()
    con = catalogue.connect(db_path)
    try:
        if not _has_table(con, "chunks"):
            raise Bm25Error(
                "the catalogue has no chunks; run `chatcde index chunk` first"
            )
        for sql in _TABLES_SQL:
            con.execute(sql)
        items = _load_items(con, include_title)
        wanted = {i.chunk_id: i.fingerprint for i in items}
        stored = dict(
            con.execute(f"SELECT chunk_id, fingerprint FROM {DOCS_TABLE}").fetchall()
        )
        stale = list(stored) if force else [
            cid for cid, fp in stored.items() if wanted.get(cid) != fp
        ]  # fmt: skip
        _delete(con, stale)
        removed = len([cid for cid in stale if cid not in wanted])
        current = set(stored) - set(stale)
        todo = [i for i in items if i.chunk_id not in current]
        skipped = len(items) - len(todo)
        log.info(
            "BM25: indexing %d of %d chunks (%d up to date%s)",
            len(todo),
            len(items),
            skipped,
            ", --force" if force else "",
        )
        for start in range(0, len(todo), _BATCH):
            _write_batch(con, todo[start : start + _BATCH], version)
            if progress:
                progress(min(start + _BATCH, len(todo)), len(todo))

        row = con.execute(
            f"SELECT count(DISTINCT term), count(*) FROM {POSTINGS_TABLE}"
        ).fetchone()
        terms, postings = (int(row[0]), int(row[1])) if row else (0, 0)
    finally:
        con.close()

    summary = Bm25Summary(
        code_version=version,
        tokenizer_version=TOKENIZER_VERSION,
        built_at=now(),
        chunks=len(items),
        indexed=len(todo),
        skipped=skipped,
        removed=removed,
        terms=terms,
        postings=postings,
        seconds=round(time.monotonic() - started, 1),
    )
    log.info(
        "BM25: indexed %d chunks (%d skipped, %d removed) in %.1f s; "
        "%d terms, %d postings",
        summary.indexed,
        summary.skipped,
        summary.removed,
        summary.seconds,
        summary.terms,
        summary.postings,
    )
    return summary


def _delete(con: duckdb.DuckDBPyConnection, chunk_ids: Sequence[str]) -> None:
    """Remove the docs and postings rows of ``chunk_ids`` in one transaction."""
    if not chunk_ids:
        return
    con.execute("BEGIN TRANSACTION")
    try:
        for table in (POSTINGS_TABLE, DOCS_TABLE):
            con.execute(
                f"DELETE FROM {table} WHERE chunk_id IN (SELECT unnest(?))",
                [list(chunk_ids)],
            )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    log.info("BM25: removed %d changed or deleted chunks", len(chunk_ids))


def _write_batch(
    con: duckdb.DuckDBPyConnection, batch: Sequence[_Item], version: str
) -> None:
    """Tokenize a batch of chunks and append their rows in one transaction.

    Postings go in as three parallel lists unnested in SQL, which is much faster
    than one ``executemany`` row per posting.
    """
    stamp = now().replace(tzinfo=None)
    terms: list[str] = []
    chunk_ids: list[str] = []
    tfs: list[int] = []
    docs: list[list[object]] = []
    for item in batch:
        tokens = tokenize(item.text)
        for term, tf in Counter(tokens).items():
            terms.append(term)
            chunk_ids.append(item.chunk_id)
            tfs.append(tf)
        docs.append(
            [
                item.chunk_id,
                item.record_id,
                len(tokens),
                item.fingerprint,
                version,
                stamp,
            ]
        )
    con.execute("BEGIN TRANSACTION")
    try:
        con.executemany(f"INSERT INTO {DOCS_TABLE} VALUES (?, ?, ?, ?, ?, ?)", docs)
        if terms:
            con.execute(
                f"INSERT INTO {POSTINGS_TABLE} "
                "SELECT unnest(?::VARCHAR[]), unnest(?::VARCHAR[]), "
                "unnest(?::INTEGER[])",
                [terms, chunk_ids, tfs],
            )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def bm25_search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    *,
    k: int,
    record_ids: Sequence[str] | None = None,
    k1: float = 1.2,
    b: float = 0.75,
) -> list[Bm25Hit]:
    """Return the ``k`` chunks with the highest BM25 score for ``query``.

    Okapi BM25 with the non-negative IDF ``ln(1 + (N - df + 0.5) / (df + 0.5))``.
    ``N``, ``df`` and the mean length are over the whole index, so a filter
    changes which chunks are returned but not their scores.

    Args:
        con: Open catalogue (read-only is enough).
        query: Search text; tokenized like the chunks.
        k: Number of hits.
        record_ids: If given, return only chunks of these records.
        k1: Term-frequency saturation.
        b: Length normalisation.

    Returns:
        Hits, best first; empty if the query has no indexable terms or the
        index does not exist.
    """
    terms = sorted(set(tokenize(query)))
    if not terms or not has_index(con):
        return []
    params: dict[str, object] = {"terms": terms, "k1": k1, "b": b, "k": k}
    restrict = ""
    if record_ids is not None:
        restrict = "WHERE d.record_id IN (SELECT unnest($ids::VARCHAR[]))"
        params["ids"] = list(record_ids)
    sql = f"""
    WITH q AS (SELECT unnest($terms::VARCHAR[]) AS term),
    stats AS (SELECT count(*)::DOUBLE AS n, avg(length)::DOUBLE AS avgdl
              FROM {DOCS_TABLE}),
    p AS (SELECT p.* FROM {POSTINGS_TABLE} p JOIN q USING (term)),
    df AS (SELECT term, count(*)::DOUBLE AS df FROM p GROUP BY term)
    SELECT p.chunk_id, d.record_id,
      sum(ln(1 + (stats.n - df.df + 0.5) / (df.df + 0.5))
          * p.tf * ($k1 + 1)
          / (p.tf + $k1 * (1 - $b + $b * d.length / stats.avgdl))) AS score
    FROM p JOIN df USING (term) JOIN {DOCS_TABLE} d USING (chunk_id) CROSS JOIN stats
    {restrict}
    GROUP BY p.chunk_id, d.record_id
    ORDER BY score DESC, p.chunk_id
    LIMIT $k
    """
    rows = con.execute(sql, params).fetchall()
    return [Bm25Hit(chunk_id=c, record_id=r, score=float(s)) for c, r, s in rows]
