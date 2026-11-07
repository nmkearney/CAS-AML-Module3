"""Tests for chunking (issue #20): pure functions and the ``index chunk`` stage."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.schemas import PageText, Provenance, Record, TextSource
from chatcde.store.chunking import (
    ChunkSettings,
    chunk_record,
    clean_text,
    estimate_tokens,
    pack_units,
    split_units,
)
from chatcde.store.index import ChunkingError, build_chunks

SETTINGS = ChunkSettings(chunk_tokens=500, overlap_tokens=50, tokens_per_word=1.4)
SMALL = ChunkSettings(chunk_tokens=30, overlap_tokens=8, tokens_per_word=1.0)
PROV = Provenance(code_version="test", created_at=datetime(2026, 11, 7, tzinfo=UTC))


def _record(**kw: object) -> Record:
    base: dict[str, object] = {
        "record_id": "r1",
        "title": "A title",
        "pub_type": "article",
        "languages": ["en"],
        "boris_url": "https://example.org/r1",
        "harvested_at": datetime(2026, 11, 7, tzinfo=UTC),
    }
    base.update(kw)
    return Record.model_validate(base)


def _page(page: int, text: str, language: str | None = None) -> PageText:
    return PageText(
        record_id="r1", page=page, text=text, language=language, provenance=PROV
    )


def _sentences(n: int, prefix: str = "Sentence") -> str:
    return " ".join(f"{prefix} number {i} has six words." for i in range(n))


# --- clean_text and token estimate -------------------------------------------


def test_clean_text_rejoins_hyphenated_words_and_drops_soft_hyphens():
    raw = "Nachhaltige Entwick-\nlung in der ­Hochschul­lehre"
    assert clean_text(raw) == "Nachhaltige Entwicklung in der Hochschullehre"


def test_clean_text_keeps_hyphen_before_capital_and_paragraphs():
    raw = "Nord-\nSüd Partnerschaft\nzweite Zeile\n\n  Neuer   Absatz "
    assert clean_text(raw) == "Nord-Süd Partnerschaft zweite Zeile\n\nNeuer Absatz"


def test_clean_text_applies_nfkc():
    assert clean_text("eﬃcient") == "efficient"  # ffi ligature


def test_estimate_tokens_counts_words_and_punctuation():
    assert estimate_tokens("Hello, world!", 1.0) == 4
    assert estimate_tokens("Hello, world!", 1.4) == 6  # ceil(5.6)
    assert estimate_tokens("", 1.4) == 0


def test_split_units_splits_sentences_and_keeps_pages():
    units = split_units("First one. Second one? Third (one).", page=4)
    assert [u.text for u in units] == ["First one.", "Second one?", "Third (one)."]
    assert {u.page for u in units} == {4}


def test_settings_reject_overlap_as_large_as_chunk():
    with pytest.raises(ValidationError):
        ChunkSettings(chunk_tokens=50, overlap_tokens=50, tokens_per_word=1.0)


# --- packing -----------------------------------------------------------------


def test_pack_units_respects_limit_and_overlaps():
    units = split_units(_sentences(20))  # 7 pieces per sentence
    groups = pack_units(units, SMALL)
    assert len(groups) > 1
    for g in groups:
        assert sum(u.pieces for u in g) <= SMALL.max_pieces
    for prev, nxt in zip(groups, groups[1:], strict=False):
        assert nxt[0] == prev[-1]  # one 7-piece sentence fits the 8-piece overlap
    covered = {u.text for g in groups for u in g}
    assert covered == {u.text for u in units}


def test_overlong_sentence_is_split_on_words():
    text = " ".join(f"w{i}" for i in range(100))  # one "sentence", 100 pieces
    groups = pack_units(split_units(text), SMALL)
    assert all(sum(u.pieces for u in g) <= SMALL.max_pieces for g in groups)
    words = " ".join(u.text for g in groups for u in g).split()
    assert set(words) == {f"w{i}" for i in range(100)}


# --- chunk_record ------------------------------------------------------------


def test_chunk_record_page_ranges_follow_page_gaps():
    pages = [_page(n, _sentences(4, f"P{n}")) for n in (1, 2, 3, 5, 6, 7)]
    chunks = chunk_record(_record(), pages, SMALL)
    assert all(c.source == TextSource.FULLTEXT for c in chunks)
    used = {n for c in chunks for n in range(c.page_start, c.page_end + 1)}  # type: ignore[arg-type]
    assert {1, 2, 3, 5, 6, 7} <= used
    for c in chunks:
        assert c.page_start is not None and c.page_end is not None
        assert c.page_start <= c.page_end
        assert c.token_count <= SMALL.chunk_tokens


def test_chunk_record_abstract_first_then_fulltext():
    rec = _record(abstract="An abstract.", abstract_source="abstract_openalex")
    chunks = chunk_record(rec, [_page(1, "Body text.")], SETTINGS)
    assert [c.chunk_id for c in chunks] == ["r1:0", "r1:1"]
    assert [c.source for c in chunks] == [
        TextSource.ABSTRACT_OPENALEX,
        TextSource.FULLTEXT,
    ]
    assert chunks[0].page_start is None
    assert chunks[1].page_start == chunks[1].page_end == 1


def test_chunk_record_metadata_only():
    rec = _record(keywords=["soil", "Nepal"], container="CDE Working Papers")
    (chunk,) = chunk_record(rec, [_page(1, "   ")], SETTINGS)
    assert chunk.source == TextSource.METADATA
    assert "A title" in chunk.text and "soil; Nepal" in chunk.text
    assert chunk.language == "en"


def test_chunk_language_prefers_page_language():
    chunks = chunk_record(_record(languages=[]), [_page(1, "Text.", "de")], SETTINGS)
    assert chunks[0].language == "de"


# --- the stage on the fixture catalogue --------------------------------------


@pytest.fixture
def catalogue_copy(fixture_db: Path, tmp_path: Path) -> Path:
    """A writable copy of the fixture catalogue for one test."""
    return Path(shutil.copy(fixture_db, tmp_path / "catalogue.duckdb"))


def _chunks(db: Path) -> list[tuple]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(
            "SELECT chunk_id, record_id, source, page_start, page_end, token_count "
            "FROM chunks ORDER BY chunk_id"
        ).fetchall()
    finally:
        con.close()


def test_every_record_gets_chunks(catalogue_copy: Path):
    s = build_chunks(catalogue_copy, SETTINGS)
    rows = _chunks(catalogue_copy)
    con = duckdb.connect(str(catalogue_copy), read_only=True)
    record_ids = {r[0] for r in con.execute("SELECT record_id FROM records").fetchall()}
    pages = set(con.execute("SELECT record_id, page FROM pages").fetchall())
    con.close()

    assert s.records == s.chunked == len(record_ids) == 34
    assert {r[1] for r in rows} == record_ids
    assert len({r[0] for r in rows}) == len(rows) == s.chunks_total
    assert max(r[5] for r in rows) <= SETTINGS.chunk_tokens

    by_record: dict[str, list[tuple]] = {}
    for r in rows:
        by_record.setdefault(r[1], []).append(r)
    for rid, rs in by_record.items():
        ns = sorted(int(r[0].rsplit(":", 1)[1]) for r in rs)
        assert ns == list(range(len(rs))), rid
    for r in rows:
        if r[2] == "fulltext":
            assert (r[1], r[3]) in pages and (r[1], r[4]) in pages

    assert [r[2] for r in by_record["fixture-synthetic-02"]] == ["metadata"]
    assert {r[2] for r in by_record["fixture-synthetic-03"]} == {"abstract_openalex"}
    assert {r[2] for r in by_record["fixture-synthetic-04"]} == {"fulltext"}


def test_second_run_skips_and_force_redoes(catalogue_copy: Path):
    build_chunks(catalogue_copy, SETTINGS)
    before = _chunks(catalogue_copy)
    again = build_chunks(catalogue_copy, SETTINGS)
    assert (again.chunked, again.skipped, again.chunks_written) == (0, 34, 0)
    forced = build_chunks(catalogue_copy, SETTINGS, force=True)
    assert forced.chunked == 34
    assert _chunks(catalogue_copy) == before


def test_new_settings_rechunk_everything(catalogue_copy: Path):
    build_chunks(catalogue_copy, SETTINGS)
    smaller = SETTINGS.model_copy(update={"chunk_tokens": 300})
    s = build_chunks(catalogue_copy, smaller)
    assert s.chunked == 34
    assert max(r[5] for r in _chunks(catalogue_copy)) <= 300


def test_limit_leaves_the_rest_for_later(catalogue_copy: Path):
    s = build_chunks(catalogue_copy, SETTINGS, limit=5)
    assert (s.chunked, s.remaining) == (5, 29)
    s = build_chunks(catalogue_copy, SETTINGS)
    assert (s.chunked, s.skipped) == (29, 5)


def test_removed_record_loses_its_chunks(catalogue_copy: Path):
    build_chunks(catalogue_copy, SETTINGS)
    con = duckdb.connect(str(catalogue_copy))
    con.execute("DELETE FROM records WHERE record_id = 'fixture-synthetic-01'")
    con.close()
    s = build_chunks(catalogue_copy, SETTINGS)
    assert s.removed == 1 and s.chunked == 0
    assert all(r[1] != "fixture-synthetic-01" for r in _chunks(catalogue_copy))


def test_changed_page_text_rechunks_only_that_record(catalogue_copy: Path):
    build_chunks(catalogue_copy, SETTINGS)
    con = duckdb.connect(str(catalogue_copy))
    con.execute(
        "UPDATE pages SET text = text || ' Added sentence.' "
        "WHERE record_id = 'fixture-synthetic-04'"
    )
    con.close()
    s = build_chunks(catalogue_copy, SETTINGS)
    assert (s.chunked, s.skipped) == (1, 33)


def test_missing_catalogue_is_a_clear_error(tmp_path: Path):
    with pytest.raises(ChunkingError, match="no catalogue"):
        build_chunks(tmp_path / "none.duckdb", SETTINGS)


def test_cli_index_chunk(catalogue_copy: Path, tmp_path: Path):
    override = tmp_path / "o.yaml"
    override.write_text(
        f'data:\n  catalogue: "{catalogue_copy.as_posix()}"\nlogging:\n  file: null\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["--config", str(override), "index", "chunk"])
    assert result.exit_code == 0, result.output
    assert "Chunks written" in result.output
    assert _chunks(catalogue_copy)
