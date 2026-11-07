"""Tests for embeddings and vector search (issue #21). No network: a fake endpoint.

The fake endpoint embeds text as a bag of hashed words (64 dimensions), so texts
that share words are close, which is enough to test search end to end.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import zlib
from pathlib import Path

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.store.chunking import ChunkSettings
from chatcde.store.embeddings import (
    EmbeddingError,
    EmbedSettings,
    build_embeddings,
    document_input,
    embed_query,
    prepare_vector,
    query_input,
    vector_search,
)
from chatcde.store.endpoint import EndpointClient, EndpointError
from chatcde.store.index import build_chunks

DIMS = 64
CHUNKING = ChunkSettings(chunk_tokens=500, overlap_tokens=50, tokens_per_word=1.4)


def fake_vector(text: str) -> list[float]:
    """Bag-of-words vector: each lower-cased word adds 1 to a hashed dimension."""
    v = [0.0] * DIMS
    for word in re.findall(r"\w+", text.lower()):
        v[zlib.crc32(word.encode()) % DIMS] += 1.0
    v[0] += 0.01  # never all zeros
    return v


class FakeEndpoint:
    """``/embeddings`` handler for ``httpx.MockTransport`` that records requests."""

    def __init__(self, fail_first: int = 0, status: int = 503) -> None:
        self.requests: list[dict] = []
        self.fail_first = fail_first
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if len(self.requests) <= self.fail_first:
            return httpx.Response(self.status, text="busy")
        data = [
            {"index": i, "embedding": fake_vector(t), "object": "embedding"}
            for i, t in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": data, "model": body["model"]})


def make_client(endpoint: FakeEndpoint, retries: int = 2) -> EndpointClient:
    return EndpointClient(
        "https://llm.test/v1",
        "secret",
        timeout_s=5,
        max_retries=retries,
        transport=httpx.MockTransport(endpoint),
        sleep=lambda _: None,
    )


def settings(**kw: object) -> EmbedSettings:
    base: dict[str, object] = {
        "model": "fake-embed",
        "batch_size": 16,
        "dimensions": None,
        "max_concurrency": 2,
    }
    base.update(kw)
    return EmbedSettings.model_validate(base)


@pytest.fixture
def chunked_db(fixture_db: Path, tmp_path: Path) -> Path:
    """A writable copy of the fixture catalogue with chunks built."""
    db = Path(shutil.copy(fixture_db, tmp_path / "catalogue.duckdb"))
    build_chunks(db, CHUNKING)
    return db


def _count(db: Path, sql: str) -> int:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return int(con.execute(sql).fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


# --- pure functions -----------------------------------------------------------


def test_prepare_vector_truncates_and_normalises():
    v = prepare_vector([3.0, 4.0, 12.0], 2)
    assert v == pytest.approx([0.6, 0.8])
    assert math.isclose(sum(x * x for x in prepare_vector([1.0, 2.0, 2.0], None)), 1.0)


def test_prepare_vector_rejects_zero():
    with pytest.raises(EmbeddingError):
        prepare_vector([0.0, 0.0], None)


def test_inputs():
    assert document_input("T", "body", True) == "T\n\nbody"
    assert document_input("T", "body", False) == "body"
    assert (
        query_input("soil?", "Find passages") == "Instruct: Find passages\nQuery: soil?"
    )
    assert query_input("soil?", None) == "soil?"


# --- endpoint client ------------------------------------------------------------


def test_client_retries_busy_server_then_succeeds():
    ep = FakeEndpoint(fail_first=2)
    vectors = make_client(ep, retries=2).embed("m", ["a", "b"])
    assert len(vectors) == 2 and len(ep.requests) == 3


def test_client_gives_up_after_retries():
    ep = FakeEndpoint(fail_first=10)
    with pytest.raises(EndpointError, match="after 3 attempts"):
        make_client(ep, retries=2).embed("m", ["a"])


def test_client_401_names_the_key_variable():
    ep = FakeEndpoint(fail_first=1, status=401)
    client = EndpointClient(
        "https://llm.test/v1",
        None,
        timeout_s=5,
        max_retries=3,
        key_hint="CHATCDE_LLM_API_KEY",
        transport=httpx.MockTransport(ep),
    )
    with pytest.raises(EndpointError, match="CHATCDE_LLM_API_KEY"):
        client.embed("m", ["a"])
    assert len(ep.requests) == 1  # not retried


def test_client_sends_bearer_token():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    client = EndpointClient(
        "https://llm.test/v1/", "k", timeout_s=5, max_retries=0,
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    client.embed("m", ["a"])
    assert seen == ["Bearer k"]


# --- the stage --------------------------------------------------------------------


def test_embeds_every_chunk_in_batches(chunked_db: Path):
    ep = FakeEndpoint()
    s = build_embeddings(chunked_db, make_client(ep), settings(dimensions=32))
    n_chunks = _count(chunked_db, "SELECT count(*) FROM chunks")
    assert s.chunks == s.embedded == s.vectors_total == n_chunks
    assert s.dims == 32
    assert s.requests == len(ep.requests) == math.ceil(n_chunks / 16)
    assert all(len(r["input"]) <= 16 for r in ep.requests)
    assert _count(chunked_db, "SELECT count(DISTINCT dims) FROM embeddings") == 1
    # Titles are part of the document input.
    assert any(
        t.startswith("Synthetic fixture:") for r in ep.requests for t in r["input"]
    )


def test_rerun_skips_force_redoes(chunked_db: Path):
    build_embeddings(chunked_db, make_client(FakeEndpoint()), settings())
    ep = FakeEndpoint()
    s = build_embeddings(chunked_db, make_client(ep), settings())
    assert (s.embedded, s.requests, len(ep.requests)) == (0, 0, 0)
    s = build_embeddings(
        chunked_db, make_client(FakeEndpoint()), settings(), force=True
    )
    assert s.embedded == s.chunks == s.vectors_total


def test_limit_then_resume(chunked_db: Path):
    s = build_embeddings(chunked_db, make_client(FakeEndpoint()), settings(), limit=20)
    assert (s.embedded, s.remaining) == (20, s.chunks - 20)
    s = build_embeddings(chunked_db, make_client(FakeEndpoint()), settings())
    assert s.embedded == s.chunks - 20 and s.skipped == 20


def test_changed_dimensions_reembed(chunked_db: Path):
    build_embeddings(chunked_db, make_client(FakeEndpoint()), settings(dimensions=32))
    s = build_embeddings(
        chunked_db, make_client(FakeEndpoint()), settings(dimensions=16)
    )
    assert s.removed == s.chunks == s.embedded
    assert s.dims == 16


def test_rechunked_record_is_reembedded(chunked_db: Path):
    build_embeddings(chunked_db, make_client(FakeEndpoint()), settings())
    con = duckdb.connect(str(chunked_db))
    con.execute(
        "UPDATE pages SET text = text || ' Extra sentence about goats.' "
        "WHERE record_id = 'fixture-synthetic-04'"
    )
    con.close()
    build_chunks(chunked_db, CHUNKING)
    s = build_embeddings(chunked_db, make_client(FakeEndpoint()), settings())
    n = _count(
        chunked_db,
        "SELECT count(*) FROM chunks WHERE record_id = 'fixture-synthetic-04'",
    )
    assert s.embedded == s.removed == n


def test_two_models_coexist(chunked_db: Path):
    build_embeddings(chunked_db, make_client(FakeEndpoint()), settings())
    build_embeddings(chunked_db, make_client(FakeEndpoint()), settings(model="other"))
    n = _count(chunked_db, "SELECT count(*) FROM chunks")
    assert _count(chunked_db, "SELECT count(*) FROM embeddings") == 2 * n


def test_failure_keeps_finished_batches(chunked_db: Path):
    class FailLater(FakeEndpoint):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if len(self.requests) >= 3:
                self.requests.append({})
                return httpx.Response(400, text="bad request")
            return super().__call__(request)

    with pytest.raises(EndpointError):
        build_embeddings(
            chunked_db, make_client(FailLater()), settings(max_concurrency=1)
        )
    assert _count(chunked_db, "SELECT count(*) FROM embeddings") == 3 * 16


def test_needs_chunks(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    with pytest.raises(EmbeddingError, match="index chunk"):
        build_embeddings(db, make_client(FakeEndpoint()), settings())


# --- search -----------------------------------------------------------------------


def test_vector_search_finds_the_matching_record(chunked_db: Path):
    client = make_client(FakeEndpoint())
    build_embeddings(chunked_db, client, settings())
    q = embed_query(
        client,
        "co-designing a living lab with urban gardeners",
        model="fake-embed",
        dimensions=None,
        instruction=None,
    )
    con = duckdb.connect(str(chunked_db), read_only=True)
    hits = vector_search(con, q, model="fake-embed", k=5)
    assert hits[0].record_id == "fixture-synthetic-04"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert -1.0 <= hits[-1].score <= hits[0].score <= 1.0 + 1e-6

    only = vector_search(
        con, q, model="fake-embed", k=5, record_ids=["fixture-synthetic-01"]
    )
    assert {h.record_id for h in only} == {"fixture-synthetic-01"}
    assert vector_search(con, q, model="unknown", k=5) == []
    con.close()


def test_cli_index_embed(
    chunked_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        f'data:\n  catalogue: "{chunked_db.as_posix()}"\nlogging:\n  file: null\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["--config", str(override), "index", "embed"])
    assert result.exit_code == 0, result.output
    assert "Embedded in this run" in result.output
    assert ep.requests and ep.requests[0]["model"] == "qwen3-embedding-4b"
    assert _count(chunked_db, "SELECT max(dims) FROM embeddings") == 64  # fake < 1024
