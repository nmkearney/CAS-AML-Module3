"""Tests for filters, the rerank client and hybrid search (issue #22). No network.

A fake endpoint answers ``/embeddings`` with hashed bag-of-words vectors (as in
``test_store_embeddings.py``) and ``/rerank`` with the share of query words found
in each document, so every stage can be checked end to end.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.schemas import Filters
from chatcde.store.bm25 import build_bm25
from chatcde.store.chunking import ChunkSettings
from chatcde.store.embeddings import EmbedSettings, build_embeddings
from chatcde.store.endpoint import EndpointClient, EndpointError
from chatcde.store.filters import FilterError, filter_record_ids, is_empty
from chatcde.store.index import build_chunks
from chatcde.store.search import (
    SearchError,
    SearchSettings,
    hybrid_search,
    rrf_merge,
)
from test_store_embeddings import fake_vector

CHUNKING = ChunkSettings(chunk_tokens=500, overlap_tokens=50, tokens_per_word=1.4)
QUERY = "co-designing a living lab with urban gardeners"
TARGET = "fixture-synthetic-04"


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


class FakeEndpoint:
    """Handles ``/embeddings`` and ``/rerank``; records each request's path and body."""

    def __init__(self, rerank_status: int = 200, rerank_body: dict | None = None):
        self.requests: list[tuple[str, dict]] = []
        self.rerank_status = rerank_status
        self.rerank_body = rerank_body

    def paths(self) -> list[str]:
        return [p for p, _ in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if request.url.path.endswith("/embeddings"):
            data = [
                {"index": i, "embedding": fake_vector(t)}
                for i, t in enumerate(body["input"])
            ]
            return httpx.Response(200, json={"data": data})
        if request.url.path.endswith("/rerank"):
            if self.rerank_status != 200:
                return httpx.Response(self.rerank_status, text="bad")
            if self.rerank_body is not None:
                return httpx.Response(200, json=self.rerank_body)
            q = _words(body["query"])
            results = [
                {"index": i, "relevance_score": len(q & _words(d)) / len(q)}
                for i, d in enumerate(body["documents"])
            ]
            return httpx.Response(200, json={"results": results})
        return httpx.Response(404)


def make_client(ep: FakeEndpoint, retries: int = 0) -> EndpointClient:
    return EndpointClient(
        "https://llm.test/v1",
        "secret",
        timeout_s=5,
        max_retries=retries,
        transport=httpx.MockTransport(ep),
        sleep=lambda _: None,
    )


def settings(**kw: object) -> SearchSettings:
    base: dict[str, object] = {
        "candidates": 20,
        "top_n": 5,
        "embedding_model": "fake-embed",
        "reranker_model": "fake-rerank",
    }
    base.update(kw)
    return SearchSettings.model_validate(base)


@pytest.fixture(scope="module")
def indexed_db(fixture_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fixture catalogue with chunks, fake vectors and a BM25 index (read it only)."""
    db = Path(shutil.copy(fixture_db, tmp_path_factory.mktemp("s") / "c.duckdb"))
    build_chunks(db, CHUNKING)
    emb = EmbedSettings(model="fake-embed", batch_size=64)
    build_embeddings(db, make_client(FakeEndpoint()), emb)
    build_bm25(db)
    return db


@pytest.fixture
def con(indexed_db: Path):
    c = duckdb.connect(str(indexed_db), read_only=True)
    yield c
    c.close()


# --- fusion ---------------------------------------------------------------------------


def test_rrf_merge():
    fused = rrf_merge([["a", "b", "c"], ["b", "d"]], k=60)
    assert [cid for cid, _ in fused] == ["b", "a", "d", "c"]
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)
    # Ties are broken by chunk ID, so results are reproducible.
    assert [cid for cid, _ in rrf_merge([["y"], ["x"]])] == ["x", "y"]
    assert rrf_merge([[], []]) == []


# --- filters ----------------------------------------------------------------------


def test_empty_filters_restrict_nothing(con):
    assert (
        is_empty(None) and is_empty(Filters()) and is_empty(Filters(min_confidence=0.9))
    )
    assert filter_record_ids(con, None) is None
    assert filter_record_ids(con, Filters()) is None


def test_record_filters(con):
    years = filter_record_ids(con, Filters(year_from=2020, year_to=2021))
    expected = con.execute(
        "SELECT record_id FROM records WHERE year BETWEEN 2020 AND 2021 "
        "ORDER BY record_id"
    ).fetchall()
    assert years == [r for (r,) in expected] and years

    de = filter_record_ids(con, Filters(languages=["DE"]))
    assert de and all(
        "de"
        in con.execute(
            "SELECT languages FROM records WHERE record_id = ?", [r]
        ).fetchone()[0]  # type: ignore[index]
        for r in de
    )
    units = filter_record_ids(con, Filters(cde_units=["sustainable governance"]))
    assert (
        units
        and len(units)
        == con.execute(
            "SELECT count(*) FROM records "
            "WHERE list_contains(cde_units, 'Sustainable Governance')"
        ).fetchone()[0]
    )  # type: ignore[index]
    books = filter_record_ids(con, Filters(pub_types=["Book"]))
    assert books and set(books) == {
        r for (r,) in con.execute(
            "SELECT record_id FROM records WHERE pub_type = 'book'"
        ).fetchall()
    }  # fmt: skip
    # AND across fields.
    both = filter_record_ids(con, Filters(languages=["de"], pub_types=["book"]))
    assert set(both or []) == set(de or []) & set(books or [])
    assert filter_record_ids(con, Filters(year_from=3000)) == []


def test_tag_filters_respect_confidence(con):
    def tagged(dim: str, term: str, conf: float) -> set[str]:
        rows = con.execute(
            "SELECT DISTINCT record_id FROM tags "
            "WHERE dimension = ? AND term = ? AND confidence >= ?",
            [dim, term, conf],
        ).fetchall()
        return {r for (r,) in rows}

    low = filter_record_ids(con, Filters(sdgs=[15], min_confidence=0.0))
    high = filter_record_ids(con, Filters(sdgs=[15], min_confidence=0.8))
    assert set(low or []) == tagged("sdg", "15", 0.0) and low
    assert set(high or []) == tagged("sdg", "15", 0.8)
    assert len(high or []) <= len(low)

    countries = filter_record_ids(con, Filters(countries=["ken"], min_confidence=0))
    assert set(countries or []) == tagged("country", "KEN", 0.0)


def test_tag_filter_without_tags_table(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    c = duckdb.connect(str(db))
    c.execute("DROP TABLE tags")
    with pytest.raises(FilterError, match="countries"):
        filter_record_ids(c, Filters(countries=["KEN"]))
    assert filter_record_ids(c, Filters(year_from=2000))  # record filters still work
    c.close()


# --- rerank client -------------------------------------------------------------------


def test_rerank_client_sorts_and_validates():
    ep = FakeEndpoint()
    scored = make_client(ep).rerank(
        "m", "soil erosion", ["gdp", "soil erosion", "soil"]
    )
    assert [i for i, _ in scored] == [1, 2, 0]
    assert ep.requests[0][0] == "/v1/rerank"
    assert ep.requests[0][1]["top_n"] == 3
    assert make_client(ep).rerank("m", "q", []) == []

    for bad in (
        {"results": []},
        {"results": [{"index": 5, "relevance_score": 0.1}]},
        {"results": [{"index": 0, "relevance_score": "x"}]},
        {"results": [{"index": 0, "relevance_score": 1}, {"index": 0, "score": 1}]},
    ):
        with pytest.raises(EndpointError):
            make_client(FakeEndpoint(rerank_body=bad)).rerank("m", "q", ["a", "b"])


def test_rerank_client_retries_busy_server():
    class Busy(FakeEndpoint):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if len(self.requests) < 1:
                self.requests.append(("busy", {}))
                return httpx.Response(503)
            return super().__call__(request)

    ep = Busy()
    assert make_client(ep, retries=1).rerank("m", "a", ["a"]) == [(0, 1.0)]


# --- hybrid search ----------------------------------------------------------------


def test_hybrid_finds_the_target_and_keeps_the_trail(con):
    ep = FakeEndpoint()
    hits = hybrid_search(con, make_client(ep), QUERY, settings())
    assert hits[0].chunk.record_id == TARGET
    assert len(hits) == 5
    top = hits[0]
    assert top.bm25_rank and top.vector_rank and top.rerank_score is not None
    assert top.score == top.rerank_score
    assert top.title.startswith("Synthetic fixture")
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert ep.paths() == ["/v1/embeddings", "/v1/rerank"]
    # Reranker gets title + text, cut to rerank_max_chars.
    docs = ep.requests[1][1]["documents"]
    assert len(docs) == 20 and all(len(d) <= 2000 for d in docs)


def test_modes(con):
    ep = FakeEndpoint()
    b = hybrid_search(con, None, QUERY, settings(mode="bm25", rerank=False))
    assert b[0].chunk.record_id == TARGET
    assert all(h.vector_rank is None and h.rerank_score is None for h in b)
    assert b[0].score == b[0].rrf_score

    v = hybrid_search(
        con, make_client(ep), QUERY, settings(mode="vector", rerank=False)
    )
    assert v[0].chunk.record_id == TARGET
    assert all(h.bm25_rank is None for h in v)
    assert ep.paths() == ["/v1/embeddings"]


def test_reranker_reorders(con):
    """Without reranking, order follows fusion; with it, the rerank score."""
    s = settings(rerank=False, top_n=20)
    fused = hybrid_search(con, make_client(FakeEndpoint()), "lab gardeners", s)
    assert [h.rrf_score for h in fused] == sorted(
        (h.rrf_score for h in fused), reverse=True
    )
    reranked = hybrid_search(
        con, make_client(FakeEndpoint()), "lab gardeners", settings(top_n=20)
    )
    assert {h.chunk.chunk_id for h in reranked} == {h.chunk.chunk_id for h in fused}
    assert [h.rerank_score for h in reranked] == sorted(
        (h.rerank_score or 0 for h in reranked), reverse=True
    )


def test_rerank_without_model_is_skipped(con):
    hits = hybrid_search(con, None, QUERY, settings(mode="bm25", reranker_model=None))
    assert hits and hits[0].rerank_score is None


def test_rerank_failure_is_an_error(con):
    with pytest.raises(EndpointError):
        hybrid_search(
            con, make_client(FakeEndpoint(rerank_status=400)), QUERY, settings()
        )


def test_filters_apply(con):
    hits = hybrid_search(
        con,
        make_client(FakeEndpoint()),
        QUERY,
        settings(),
        Filters(languages=["de"]),
    )
    de = set(filter_record_ids(con, Filters(languages=["de"])) or [])
    assert hits and {h.chunk.record_id for h in hits} <= de
    assert TARGET not in de
    assert hybrid_search(con, None, QUERY, settings(), Filters(year_from=3000)) == []


def test_needs_a_client(con):
    with pytest.raises(SearchError, match="client"):
        hybrid_search(con, None, QUERY, settings())
    assert hybrid_search(con, None, "   ", settings()) == []


def test_fallbacks_and_missing_indexes(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    build_chunks(db, CHUNKING)
    c = duckdb.connect(str(db), read_only=True)
    with pytest.raises(SearchError, match="no search index"):
        hybrid_search(c, None, QUERY, settings())
    with pytest.raises(SearchError, match="index bm25"):
        hybrid_search(c, None, QUERY, settings(mode="bm25"))
    c.close()

    build_bm25(db)
    c = duckdb.connect(str(db), read_only=True)
    with pytest.raises(SearchError, match="index embed"):
        hybrid_search(c, None, QUERY, settings(mode="vector"))
    # Hybrid without vectors: BM25 only, no embedding request.
    ep = FakeEndpoint()
    hits = hybrid_search(c, make_client(ep), QUERY, settings())
    assert hits[0].chunk.record_id == TARGET
    assert ep.paths() == ["/v1/rerank"]
    c.close()


def test_settings_from_config():
    from chatcde.config import load_config

    cfg = load_config()
    s = SearchSettings.from_config(cfg)
    assert s.candidates == cfg.retrieval.top_k and s.top_n == cfg.retrieval.top_n
    assert s.embedding_model == cfg.models.embedding
    assert s.query_instruction == cfg.embedding.query_instructions[cfg.models.embedding]
    assert s.reranker_model == cfg.models.reranker and s.will_rerank
    alt = SearchSettings.from_config(cfg, embedding_model="other", rerank=False)
    assert alt.query_instruction is None and alt.needs_client
    assert not SearchSettings.from_config(cfg, mode="bm25", rerank=False).needs_client


def test_cli_index_search_hybrid(
    indexed_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from chatcde.store import endpoint

    ep = FakeEndpoint()
    real_init = endpoint.EndpointClient.__init__

    def init_with_fake(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = httpx.MockTransport(ep)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(endpoint.EndpointClient, "__init__", init_with_fake)
    override = tmp_path / "o.yaml"
    override.write_text(
        f'data:\n  catalogue: "{indexed_db.as_posix()}"\n'
        "models:\n  embedding: fake-embed\nlogging:\n  file: null\n",
        encoding="utf-8",
    )
    result = CliRunner(env={"COLUMNS": "250"}).invoke(
        app, ["--config", str(override), "index", "search", QUERY, "--top-n", "3"]
    )
    assert result.exit_code == 0, result.output
    assert TARGET in result.output and "reranked" in result.output
    assert ep.paths() == ["/v1/embeddings", "/v1/rerank"]

    result = CliRunner(env={"COLUMNS": "250"}).invoke(
        app, ["--config", str(override), "index", "search", QUERY, "--mode", "x"]
    )
    assert result.exit_code == 2
