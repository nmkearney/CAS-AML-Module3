"""Hybrid search: BM25 + vectors, fused, then reranked (issue #22).

The retrieval engine behind ``search_publications`` (B4). One query runs:

1. **Filters** -> record IDs (:func:`chatcde.store.filters.filter_record_ids`).
2. **BM25** (:func:`chatcde.store.bm25.bm25_search`) and **vector search**
   (:func:`chatcde.store.embeddings.vector_search`), each ``candidates`` deep.
3. **Reciprocal rank fusion**: each chunk scores ``sum(1 / (rrf_k + rank))`` over
   the lists it appears in. Ranks, not scores, are fused because BM25 and cosine
   scores are on different scales.
4. **Reranking** of the fused candidates with the ``reranker`` role model, which
   reads query and passage together and is the most precise (and slowest) step.
5. The best ``top_n`` chunks, with every intermediate rank and score kept, so
   ``chatcde evaluate questions`` (#28) can compare the stages.

If one retriever has no index (no vectors for the model, or no BM25 index), the
other is used alone with a warning; a failing reranker is an error, not a
silent fallback (``rerank=False`` is the explicit way to skip it). See
docs/decisions/0013-hybrid-search.md.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, Field

from chatcde.config import Config
from chatcde.schemas import Chunk, Filters
from chatcde.store import bm25
from chatcde.store.embeddings import (
    TABLE as EMBEDDINGS_TABLE,
)
from chatcde.store.embeddings import (
    document_input,
    embed_query,
    vector_search,
)
from chatcde.store.endpoint import EndpointClient
from chatcde.store.filters import filter_record_ids

log = logging.getLogger(__name__)

SearchMode = Literal["hybrid", "bm25", "vector"]


class SearchError(Exception):
    """The search cannot run: no index for the chosen mode, or no client."""


class SearchSettings(BaseModel):
    """Everything that shapes one search (``retrieval``, ``models``, ``embedding``)."""

    mode: SearchMode = "hybrid"
    candidates: int = Field(ge=1, description="Depth of each retriever and fusion")
    top_n: int = Field(ge=1, description="Chunks returned")
    rrf_k: int = Field(default=60, ge=1)
    rerank: bool = True
    embedding_model: str
    dimensions: int | None = None
    query_instruction: str | None = None
    include_title: bool = True
    reranker_model: str | None = None
    rerank_path: str = "/rerank"
    rerank_max_chars: int = Field(default=2000, ge=100)
    bm25_k1: float = 1.2
    bm25_b: float = 0.75

    @classmethod
    def from_config(cls, cfg: Config, **overrides: Any) -> SearchSettings:
        """Build settings from the config; keyword arguments override fields."""
        model = cfg.models.embedding
        values: dict[str, Any] = {
            "candidates": cfg.retrieval.top_k,
            "top_n": cfg.retrieval.top_n,
            "rrf_k": cfg.retrieval.rrf_k,
            "rerank": cfg.retrieval.rerank,
            "embedding_model": model,
            "dimensions": cfg.embedding.dimensions,
            "query_instruction": cfg.embedding.query_instructions.get(model),
            "include_title": cfg.embedding.include_title,
            "reranker_model": cfg.models.reranker,
            "rerank_path": cfg.llm.rerank_path,
            "rerank_max_chars": cfg.retrieval.rerank_max_chars,
            "bm25_k1": cfg.retrieval.bm25_k1,
            "bm25_b": cfg.retrieval.bm25_b,
        }
        values.update(overrides)
        if "embedding_model" in overrides and "query_instruction" not in overrides:
            values["query_instruction"] = cfg.embedding.query_instructions.get(
                overrides["embedding_model"]
            )
        return cls.model_validate(values)

    @property
    def needs_client(self) -> bool:
        """True if this search will call the endpoint (query embedding or rerank)."""
        return self.mode != "bm25" or self.will_rerank

    @property
    def will_rerank(self) -> bool:
        """True if reranking is on and a reranker model is set."""
        return self.rerank and bool(self.reranker_model)


class RankedChunk(BaseModel):
    """One search result with the trail of how it got its place.

    ``score`` is the rerank score when reranking ran, else the fusion score.
    Ranks are 1-based; ``None`` means the chunk was not in that list.
    """

    chunk: Chunk
    title: str
    score: float
    rrf_score: float
    bm25_rank: int | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    vector_score: float | None = None
    rerank_score: float | None = None


def rrf_merge(lists: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    """Fuse ranked lists of chunk IDs by reciprocal rank fusion.

    Args:
        lists: Each list is chunk IDs, best first.
        k: Damping constant; larger values flatten the advantage of top ranks.

    Returns:
        ``(chunk_id, score)`` pairs, best first; ties broken by chunk ID.
    """
    scores: dict[str, float] = {}
    for ranked in lists:
        for rank, chunk_id in enumerate(ranked, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda t: (-t[1], t[0]))


def _has_vectors(con: duckdb.DuckDBPyConnection, model: str) -> bool:
    """True if the catalogue holds vectors of ``model``."""
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    row = con.execute(sql, [EMBEDDINGS_TABLE]).fetchone()
    if not (row and row[0]):
        return False
    row = con.execute(
        f"SELECT count(*) FROM {EMBEDDINGS_TABLE} WHERE model = ?", [model]
    ).fetchone()
    return bool(row and row[0])


def _retrievers(
    con: duckdb.DuckDBPyConnection, settings: SearchSettings
) -> tuple[bool, bool]:
    """Decide which retrievers run: ``(use_bm25, use_vectors)``.

    Raises:
        SearchError: If the chosen mode has no usable index.
    """
    have_bm25 = bm25.has_index(con)
    have_vec = _has_vectors(con, settings.embedding_model)
    if settings.mode == "bm25":
        if not have_bm25:
            raise SearchError("no BM25 index; run `chatcde index bm25`")
        return True, False
    if settings.mode == "vector":
        if not have_vec:
            raise SearchError(
                f"no vectors for {settings.embedding_model}; run `chatcde index embed`"
            )
        return False, True
    if not have_bm25 and not have_vec:
        raise SearchError(
            "no search index; run `chatcde index embed` and `chatcde index bm25`"
        )
    if not have_vec:
        log.warning(
            "no vectors for %s; searching with BM25 only", settings.embedding_model
        )
    if not have_bm25:
        log.warning("no BM25 index; searching with vectors only")
    return have_bm25, have_vec


def _load_chunks(
    con: duckdb.DuckDBPyConnection, chunk_ids: Sequence[str]
) -> dict[str, tuple[Chunk, str]]:
    """Load chunks and their record titles by ID."""
    cur = con.execute(
        "SELECT c.*, coalesce(r.title, '') AS _title FROM chunks c "
        "LEFT JOIN records r USING (record_id) "
        "WHERE c.chunk_id IN (SELECT unnest(?::VARCHAR[]))",
        [list(chunk_ids)],
    )
    names = [d[0] for d in cur.description]
    out: dict[str, tuple[Chunk, str]] = {}
    for row in cur.fetchall():
        data = dict(zip(names, row, strict=True))
        title = data.pop("_title")
        chunk = Chunk.model_validate(data)
        out[chunk.chunk_id] = (chunk, title)
    return out


def hybrid_search(
    con: duckdb.DuckDBPyConnection,
    client: EndpointClient | None,
    query: str,
    settings: SearchSettings,
    filters: Filters | None = None,
) -> list[RankedChunk]:
    """Find the chunks that best answer ``query``.

    Args:
        con: Open catalogue (read-only is enough).
        client: Endpoint client for the query embedding and the reranker; may be
            ``None`` only when ``settings.needs_client`` is false.
        query: Search text, in any language.
        settings: Mode, depths, models (see :meth:`SearchSettings.from_config`).
        filters: Optional restrictions, applied before ranking.

    Returns:
        Up to ``settings.top_n`` chunks, best first.

    Raises:
        SearchError: If the mode has no index or a needed client is missing.
        chatcde.store.filters.FilterError: If tag filters are set without tags.
        chatcde.store.endpoint.EndpointError: If embedding or reranking fails.
    """
    if not query.strip():
        return []
    record_ids = filter_record_ids(con, filters)
    if record_ids is not None and not record_ids:
        log.info("no record matches the filters")
        return []
    use_bm25, use_vec = _retrievers(con, settings)
    if client is None and (use_vec or settings.will_rerank):
        raise SearchError("this search needs the endpoint, but no client was given")

    bm25_hits = (
        bm25.bm25_search(
            con,
            query,
            k=settings.candidates,
            record_ids=record_ids,
            k1=settings.bm25_k1,
            b=settings.bm25_b,
        )
        if use_bm25
        else []
    )
    vec_hits = []
    if use_vec:
        assert client is not None
        qv = embed_query(
            client,
            query,
            model=settings.embedding_model,
            dimensions=settings.dimensions,
            instruction=settings.query_instruction,
        )
        vec_hits = vector_search(
            con,
            qv,
            model=settings.embedding_model,
            k=settings.candidates,
            record_ids=record_ids,
        )
    fused = rrf_merge(
        [[h.chunk_id for h in bm25_hits], [h.chunk_id for h in vec_hits]],
        settings.rrf_k,
    )[: settings.candidates]
    log.debug(
        "search %r: %d BM25, %d vector, %d fused candidates",
        query,
        len(bm25_hits),
        len(vec_hits),
        len(fused),
    )
    if not fused:
        return []

    chunks = _load_chunks(con, [cid for cid, _ in fused])
    bm25_by_id = {h.chunk_id: (r, h.score) for r, h in enumerate(bm25_hits, 1)}
    vec_by_id = {h.chunk_id: (r, h.score) for r, h in enumerate(vec_hits, 1)}
    results: list[RankedChunk] = []
    for chunk_id, rrf in fused:
        if chunk_id not in chunks:  # deleted between index and search
            continue
        chunk, title = chunks[chunk_id]
        b_rank, b_score = bm25_by_id.get(chunk_id, (None, None))
        v_rank, v_score = vec_by_id.get(chunk_id, (None, None))
        results.append(
            RankedChunk(
                chunk=chunk,
                title=title,
                score=rrf,
                rrf_score=rrf,
                bm25_rank=b_rank,
                bm25_score=b_score,
                vector_rank=v_rank,
                vector_score=v_score,
            )
        )

    if settings.will_rerank and results:
        assert client is not None and settings.reranker_model
        docs = [
            document_input(r.title, r.chunk.text, settings.include_title)[
                : settings.rerank_max_chars
            ]
            for r in results
        ]
        scored = client.rerank(
            settings.reranker_model, query, docs, path=settings.rerank_path
        )
        for index, score in scored:
            results[index].rerank_score = score
            results[index].score = score
        # Reranked first (by rerank score); any the server left out keep fusion order.
        ranked = {index for index, _ in scored}
        results = [results[i] for i, _ in scored] + [
            r for i, r in enumerate(results) if i not in ranked
        ]
    return results[: settings.top_n]
