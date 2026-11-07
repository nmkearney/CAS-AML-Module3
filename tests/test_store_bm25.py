"""Tests for the BM25 keyword index (issue #22). Local only, no endpoint."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.store.bm25 import (
    Bm25Error,
    bm25_search,
    build_bm25,
    has_index,
    tokenize,
)
from chatcde.store.chunking import ChunkSettings
from chatcde.store.index import build_chunks

CHUNKING = ChunkSettings(chunk_tokens=500, overlap_tokens=50, tokens_per_word=1.4)


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


# --- tokenizer --------------------------------------------------------------------


def test_tokenize_folds_case_and_accents():
    assert tokenize("Bodenerosion in LÄNDERN, Côte d'Ivoire") == [
        "bodenerosion",
        "landern",
        "cote",
        "ivoire",
    ]
    assert tokenize("Straße") == ["strasse"]


def test_tokenize_drops_stopwords_keeps_digits():
    assert tokenize("The role of SDG 5 in 2020 and für die Schweiz") == [
        "role",
        "sdg",
        "5",
        "2020",
        "schweiz",
    ]
    assert tokenize("the and of") == []


def test_tokenize_keeps_non_latin_scripts():
    assert tokenize("ລາວ 老挝") == ["ລາວ", "老挝"]


# --- the stage ----------------------------------------------------------------------


def test_build_indexes_every_chunk(chunked_db: Path):
    s = build_bm25(chunked_db)
    n = _count(chunked_db, "SELECT count(*) FROM chunks")
    assert s.chunks == s.indexed == n and s.skipped == 0
    assert _count(chunked_db, "SELECT count(*) FROM bm25_docs") == n
    assert s.postings == _count(chunked_db, "SELECT count(*) FROM bm25_postings")
    assert s.terms > 100


def test_rerun_skips_force_redoes(chunked_db: Path):
    first = build_bm25(chunked_db)
    s = build_bm25(chunked_db)
    assert (s.indexed, s.skipped, s.removed) == (0, s.chunks, 0)
    assert s.postings == first.postings
    s = build_bm25(chunked_db, force=True)
    assert s.indexed == s.chunks and s.postings == first.postings


def test_changed_and_deleted_chunks(chunked_db: Path):
    build_bm25(chunked_db)
    con = duckdb.connect(str(chunked_db))
    con.execute(
        "UPDATE pages SET text = text || ' Extra sentence about zebu goats.' "
        "WHERE record_id = 'fixture-synthetic-04'"
    )
    con.execute("DELETE FROM pages WHERE record_id = 'fixture-synthetic-01'")
    con.execute("DELETE FROM records WHERE record_id = 'fixture-synthetic-01'")
    con.close()
    build_chunks(chunked_db, CHUNKING)
    s = build_bm25(chunked_db)
    assert s.removed >= 1 and s.indexed >= 1
    assert (
        _count(
            chunked_db,
            "SELECT count(*) FROM bm25_docs WHERE record_id = 'fixture-synthetic-01'",
        )
        == 0
    )
    con = duckdb.connect(str(chunked_db), read_only=True)
    hits = bm25_search(con, "zebu", k=3)
    assert hits and hits[0].record_id == "fixture-synthetic-04"
    con.close()


def test_title_is_indexed_unless_turned_off(chunked_db: Path):
    build_bm25(chunked_db, include_title=False)
    without = _count(chunked_db, "SELECT sum(length) FROM bm25_docs")
    s = build_bm25(chunked_db, include_title=True)  # input changed: all re-indexed
    assert s.indexed == s.chunks
    assert _count(chunked_db, "SELECT sum(length) FROM bm25_docs") > without


def test_needs_chunks(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    with pytest.raises(Bm25Error, match="index chunk"):
        build_bm25(db)
    with pytest.raises(Bm25Error, match="no catalogue"):
        build_bm25(tmp_path / "missing.duckdb")


# --- search -----------------------------------------------------------------------


def test_search_ranks_the_matching_record_first(chunked_db: Path):
    build_bm25(chunked_db)
    con = duckdb.connect(str(chunked_db), read_only=True)
    hits = bm25_search(con, "co-designing a living lab with urban gardeners", k=5)
    assert hits[0].record_id == "fixture-synthetic-04"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert all(h.score > 0 for h in hits)

    only = bm25_search(con, "living lab", k=5, record_ids=["fixture-synthetic-01"])
    assert {h.record_id for h in only} <= {"fixture-synthetic-01"}
    assert bm25_search(con, "the of and", k=5) == []
    assert bm25_search(con, "living lab", k=5, record_ids=[]) == []
    con.close()


def test_search_without_index(chunked_db: Path):
    con = duckdb.connect(str(chunked_db), read_only=True)
    assert not has_index(con)
    assert bm25_search(con, "living lab", k=5) == []
    con.close()


def test_rarer_terms_weigh_more(chunked_db: Path):
    """A chunk with the rare word beats one with only a common word."""
    build_bm25(chunked_db)
    con = duckdb.connect(str(chunked_db), read_only=True)
    rare = bm25_search(con, "gardeners", k=1)[0]
    both = bm25_search(con, "gardeners research", k=1)[0]
    assert rare.chunk_id == both.chunk_id
    con.close()


def test_cli_index_bm25(chunked_db: Path, tmp_path: Path):
    override = tmp_path / "o.yaml"
    override.write_text(
        f'data:\n  catalogue: "{chunked_db.as_posix()}"\nlogging:\n  file: null\n',
        encoding="utf-8",
    )
    result = CliRunner(env={"COLUMNS": "250"}).invoke(
        app, ["--config", str(override), "index", "bm25"]
    )
    assert result.exit_code == 0, result.output
    assert "Indexed in this run" in result.output
    result = CliRunner(env={"COLUMNS": "250"}).invoke(
        app,
        ["--config", str(override), "index", "search", "living lab gardeners",
         "--mode", "bm25", "--no-rerank", "--top-n", "3"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "fixture-synthetic-04" in result.output
    assert "3 results" in result.output
