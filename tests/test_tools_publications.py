"""Tests for search_publications, get_publication and read_section (issue #23).

Run against the fixture catalogue with chunks, fake vectors and a BM25 index;
the endpoint is the fake from ``test_store_search.py`` (no network).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.schemas import Chunk, Filters, Record, TextSource
from chatcde.store.bm25 import build_bm25
from chatcde.store.chunking import ChunkSettings
from chatcde.store.embeddings import EmbedSettings, build_embeddings
from chatcde.store.index import build_chunks
from chatcde.tools import core
from chatcde.tools.context import ToolContext, ToolError, get_context, use_context
from chatcde.tools.publications import citation, normalise_doi
from test_store_search import FakeEndpoint, make_client

CHUNKING = ChunkSettings(chunk_tokens=500, overlap_tokens=50, tokens_per_word=1.4)
QUERY = "co-designing a living lab with urban gardeners"
TARGET = "fixture-synthetic-04"
# A fixture record with a publisher DOI, a BORIS DOI, a handle and pages 1-3.
MRD = "2760e535-c973-4524-ab84-3a1c71b9bf3a"


@pytest.fixture(scope="module")
def indexed_db(fixture_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fixture catalogue with chunks, fake vectors and a BM25 index (read only)."""
    db = Path(shutil.copy(fixture_db, tmp_path_factory.mktemp("t") / "c.duckdb"))
    build_chunks(db, CHUNKING)
    emb = EmbedSettings(model="fake-embed", batch_size=64)
    build_embeddings(db, make_client(FakeEndpoint()), emb)
    build_bm25(db)
    return db


def make_config(tmp_path: Path, db: Path, extra: str = "") -> Path:
    """Write a config override that points at ``db`` and the fake models."""
    path = tmp_path / "o.yaml"
    path.write_text(
        f'data:\n  catalogue: "{db.as_posix()}"\n'
        "models:\n  embedding: fake-embed\n  reranker: fake-rerank\n"
        "logging:\n  file: null\n" + extra,
        encoding="utf-8",
    )
    return path


def config(tmp_path: Path, db: Path, extra: str = "") -> Config:
    return load_config(make_config(tmp_path, db, extra))


@pytest.fixture
def ctx(indexed_db: Path, tmp_path: Path):
    """An active tool context on the indexed fixtures with a fake endpoint."""
    ep = FakeEndpoint()
    with use_context(ToolContext(config(tmp_path, indexed_db), make_client(ep))) as c:
        c.fake = ep  # type: ignore[attr-defined]
        yield c


def _record(db: Path, record_id: str) -> Record:
    con = duckdb.connect(str(db), read_only=True)
    cur = con.execute("SELECT * FROM records WHERE record_id = ?", [record_id])
    names = [d[0] for d in cur.description]
    row = dict(zip(names, cur.fetchone(), strict=True))  # type: ignore[arg-type]
    con.close()
    return Record.model_validate(row)


# --- helpers ------------------------------------------------------------------------


def test_normalise_doi():
    assert normalise_doi("https://doi.org/10.1659/MRD.4204") == "10.1659/mrd.4204"
    assert normalise_doi("doi:10.48350/178836.") == "10.48350/178836"
    assert normalise_doi("2760e535-c973-4524-ab84-3a1c71b9bf3a") is None


def test_citation_prefers_the_publisher_doi(indexed_db: Path):
    rec = _record(indexed_db, MRD)
    assert citation(rec, 3).doi == "10.1659/mrd.4204" and citation(rec, 3).page == 3
    bare = rec.model_copy(update={"publisher_doi": None})
    assert citation(bare).doi == rec.doi and citation(bare).page is None


# --- context ------------------------------------------------------------------------


def test_use_context_nests_and_restores(indexed_db: Path, tmp_path: Path):
    outer = ToolContext(config(tmp_path, indexed_db))
    inner = ToolContext(config(tmp_path, indexed_db))
    with use_context(outer):
        assert get_context() is outer
        with use_context(inner):
            assert get_context() is inner
        assert get_context() is outer
    assert get_context() not in (outer, inner)  # the default, from the config files
    assert isinstance(get_context().cfg, Config)


def test_missing_catalogue_is_a_tool_error(tmp_path: Path):
    with use_context(ToolContext(config(tmp_path, tmp_path / "none.duckdb"))):
        with pytest.raises(ToolError, match="no catalogue"):
            core.get_publication(MRD)


# --- search_publications --------------------------------------------------------------


def test_search_returns_cited_passages(ctx: ToolContext, indexed_db: Path):
    passages = core.search_publications(QUERY, k=4)
    assert len(passages) == 4
    top = passages[0]
    assert top.chunk.record_id == TARGET == top.citation.record_id
    assert top.citation.title.startswith("Synthetic fixture")
    assert top.citation.page == top.chunk.page_start
    rec = _record(indexed_db, TARGET)
    assert top.citation.boris_url == rec.boris_url and top.citation.year == rec.year
    assert [p.score for p in passages] == sorted(
        (p.score for p in passages), reverse=True
    )
    paths = ctx.fake.paths()  # type: ignore[attr-defined]
    assert paths == ["/v1/embeddings", "/v1/rerank"]


def test_search_applies_filters(ctx: ToolContext):
    passages = core.search_publications(QUERY, Filters(languages=["de"]), k=5)
    assert passages
    assert all(p.chunk.record_id != TARGET for p in passages)
    assert core.search_publications(QUERY, Filters(year_from=3000)) == []


def test_k_is_checked_and_capped(indexed_db: Path, tmp_path: Path):
    cfg = config(tmp_path, indexed_db, "tools:\n  max_k: 3\n")
    with use_context(ToolContext(cfg, make_client(FakeEndpoint()))):
        assert len(core.search_publications(QUERY, k=10)) == 3
        with pytest.raises(ToolError, match="at least 1"):
            core.search_publications(QUERY, k=0)
        with pytest.raises(ToolError, match="empty query"):
            core.search_publications("  ")


def test_search_errors_become_tool_errors(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    build_chunks(db, CHUNKING)
    cfg = config(tmp_path, db)
    with use_context(ToolContext(cfg, make_client(FakeEndpoint()))):
        with pytest.raises(ToolError, match="no search index"):
            core.search_publications(QUERY)
    build_bm25(db)
    with use_context(ToolContext(cfg, make_client(FakeEndpoint(rerank_status=400)))):
        with pytest.raises(ToolError, match="endpoint failed"):
            core.search_publications(QUERY)
    c = duckdb.connect(str(db))
    c.execute("DROP TABLE tags")
    c.close()
    with use_context(ToolContext(cfg, make_client(FakeEndpoint()))):
        with pytest.raises(ToolError, match="need tags"):
            core.search_publications(QUERY, Filters(countries=["KEN"]))


# --- get_publication ------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        MRD,
        f"  {MRD} ",
        "10.1659/mrd.4204",
        "https://doi.org/10.1659/MRD.4204",
        "doi:10.48350/178836",  # BORIS's own DOI
        "20.500.12422/121642",  # handle
        "https://boris-portal.unibe.ch/handle/20.500.12422/121642",
    ],
)
def test_get_publication_resolves_ids(ctx: ToolContext, ref: str):
    assert core.get_publication(ref).record.record_id == MRD


def test_get_publication_details(ctx: ToolContext):
    d = core.get_publication(MRD)
    assert d.record.publisher_doi == "10.1659/mrd.4204"
    assert [a.position for a in d.authors] == sorted(a.position for a in d.authors)
    assert d.authors and any(a.is_cde for a in d.authors)
    assert d.tags and all(t.record_id == MRD for t in d.tags)
    keys = [(t.dimension.value, -t.confidence) for t in d.tags]
    assert keys == sorted(keys)
    assert d.download is not None and d.download.record_id == MRD


def test_get_publication_unknown(ctx: ToolContext):
    with pytest.raises(ToolError, match="search_publications"):
        core.get_publication("10.9999/not-there")
    with pytest.raises(ToolError, match="empty"):
        core.get_publication(" ")


def test_get_publication_without_optional_tables(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    c = duckdb.connect(str(db))
    for table in ("authors", "tags", "downloads"):
        c.execute(f"DROP TABLE {table}")
    c.close()
    with use_context(ToolContext(config(tmp_path, db))):
        d = core.get_publication(MRD)
    assert (d.authors, d.tags, d.download) == ([], [], None)


# --- read_section ---------------------------------------------------------------------


def _fulltext_chunks(db: Path, record_id: str) -> list[Chunk]:
    con = duckdb.connect(str(db), read_only=True)
    cur = con.execute(
        "SELECT * FROM chunks WHERE record_id = ? AND source = 'fulltext'",
        [record_id],
    )
    names = [d[0] for d in cur.description]
    rows = [
        Chunk.model_validate(dict(zip(names, r, strict=True))) for r in cur.fetchall()
    ]
    con.close()
    return rows


def test_read_section_returns_overlapping_chunks_verbatim(
    ctx: ToolContext, indexed_db: Path
):
    stored = {c.chunk_id: c for c in _fulltext_chunks(indexed_db, MRD)}
    one = core.read_section(MRD, 2)
    assert one and all(
        c.page_start <= 2 <= (c.page_end or c.page_start)  # type: ignore[operator]
        for c in one
    )
    assert all(c == stored[c.chunk_id] for c in one)  # verbatim
    assert all(c.source is TextSource.FULLTEXT for c in one)
    every = core.read_section(MRD, 1, 10)
    assert {c.chunk_id for c in every} == set(stored)
    numbers = [int(c.chunk_id.rsplit(":", 1)[1]) for c in every]
    assert numbers == sorted(numbers)  # reading order
    assert core.read_section("10.1659/mrd.4204", 2) == one  # DOI works too


def test_read_section_errors(ctx: ToolContext):
    with pytest.raises(ToolError, match="no full text.*restricted"):
        core.read_section("fixture-synthetic-01", 1)
    with pytest.raises(ToolError, match="covers pages 1-3"):
        core.read_section(MRD, 7, 9)
    with pytest.raises(ToolError, match="too wide"):
        core.read_section(MRD, 1, 11)
    with pytest.raises(ToolError, match="bad page range"):
        core.read_section(MRD, 3, 2)
    with pytest.raises(ToolError, match="bad page range"):
        core.read_section(MRD, 0)
    with pytest.raises(ToolError, match="no CDE publication"):
        core.read_section("nope", 1)


def test_read_section_needs_chunks(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    with use_context(ToolContext(config(tmp_path, db))):
        with pytest.raises(ToolError, match="index chunk"):
            core.read_section(MRD, 1)


# --- CLI ----------------------------------------------------------------------------


@pytest.fixture
def cli(indexed_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Invoke ``chatcde`` with the fixture config and a fake endpoint."""
    from chatcde.store import endpoint

    real_init = endpoint.EndpointClient.__init__

    def init_with_fake(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = httpx.MockTransport(FakeEndpoint())
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(endpoint.EndpointClient, "__init__", init_with_fake)
    override = make_config(tmp_path, indexed_db)
    runner = CliRunner(env={"COLUMNS": "200"})
    return lambda *args: runner.invoke(app, ["--config", str(override), *args])


def test_cli_search(cli):
    result = cli("search", QUERY, "-k", "3")
    assert result.exit_code == 0, result.output
    assert any(line.startswith("1. [") for line in result.output.splitlines())
    assert TARGET in result.output
    result = cli("search", QUERY, "-k", "2", "--json")
    data = json.loads(result.stdout)
    assert len(data) == 2 and data[0]["citation"]["record_id"] == TARGET
    result = cli("search", QUERY, "--year-from", "3000")
    assert result.exit_code == 0 and "No passages found" in result.output


def test_cli_show(cli):
    result = cli("show", "https://doi.org/10.1659/mrd.4204")
    assert result.exit_code == 0, result.output
    assert "Mathez-Stiefel" in result.output and "Tags (confidence)" in result.output
    data = json.loads(cli("show", MRD, "--json").stdout)
    assert data["record"]["record_id"] == MRD
    result = cli("show", "nope")
    assert result.exit_code == 1 and "Error: no CDE publication" in result.output


def test_cli_read(cli):
    result = cli("read", MRD, "2")
    assert result.exit_code == 0, result.output
    assert f"({MRD}:" in result.output
    data = json.loads(cli("read", MRD, "1", "3", "--json").stdout)
    assert data and all(c["record_id"] == MRD for c in data)
    result = cli("read", MRD, "7")
    assert result.exit_code == 1 and "covers pages" in result.output
