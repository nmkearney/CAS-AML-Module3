"""Tests for list_vocabulary, count_by and find_gaps (issue #24). No network.

Gap arithmetic is checked on a small synthetic catalogue where every expected
count can be worked out by hand; vocabulary listing and the CLI run on the
fixtures.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from chatcde import catalogue
from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.schemas import Dimension, Filters, Provenance, Record, Tag, TextSource
from chatcde.tools import core
from chatcde.tools.context import ToolContext, ToolError, use_context
from chatcde.tools.counts import (
    count_matrix,
    dimension_overview,
    gap_basis,
    gap_cells,
    gap_report,
)
from chatcde.tools.vocabulary import Entry, dimension_entries

NOW = datetime(2026, 11, 8, tzinfo=UTC)
PROV = Provenance(code_version="test", created_at=NOW)
IA, ME = Dimension.IMPACT_AREA, Dimension.METHOD


def make_config(tmp_path: Path, db: Path, extra: str = "") -> Path:
    path = tmp_path / "o.yaml"
    path.write_text(
        f'data:\n  catalogue: "{db.as_posix()}"\nlogging:\n  file: null\n' + extra,
        encoding="utf-8",
    )
    return path


def config(tmp_path: Path, db: Path, extra: str = "") -> Config:
    return load_config(make_config(tmp_path, db, extra))


def rec(rid: str, pub_type: str = "article", year: int = 2020) -> Record:
    return Record(
        record_id=rid,
        title=f"Record {rid}",
        year=year,
        pub_type=pub_type,
        boris_url=f"https://boris.test/{rid}",
        harvested_at=NOW,
    )


def tag(rid: str, dim: Dimension, term: str, conf: float = 0.9) -> Tag:
    return Tag(
        record_id=rid,
        dimension=dim,
        term=term,
        confidence=conf,
        source=TextSource.ABSTRACT_BORIS,
        provenance=PROV,
    )


@pytest.fixture
def toy_db(tmp_path: Path) -> Path:
    """20 tagged articles in a perfect checkerboard, plus edge cases.

    * a01-a10: impact area A, method x;  b01-b10: impact area B, method y.
      So A-y and B-x never occur; every expected count is 10 x 10 / 20 = 5.
    * c01: a conference item tagged A and y.
    * u01: no tags at all.  u02: impact area A only (no method).
    * a01 also has a low-confidence method y (0.3), which must not count.
    * old: a 1990 article tagged B and x.
    """
    records = [rec(f"a{i:02}") for i in range(1, 11)]
    records += [rec(f"b{i:02}") for i in range(1, 11)]
    records += [
        rec("c01", "conference_item"),
        rec("u01"),
        rec("u02"),
        rec("old", year=1990),
    ]
    tags = []
    for i in range(1, 11):
        tags += [tag(f"a{i:02}", IA, "A"), tag(f"a{i:02}", ME, "x")]
        tags += [tag(f"b{i:02}", IA, "B"), tag(f"b{i:02}", ME, "y")]
    tags += [tag("c01", IA, "A"), tag("c01", ME, "y"), tag("u02", IA, "A")]
    tags += [tag("a01", ME, "y", conf=0.3)]
    tags += [tag("old", IA, "B"), tag("old", ME, "x")]
    db = tmp_path / "toy.duckdb"
    con = catalogue.connect(db)
    catalogue.ensure_tables(con, ["records", "tags"])
    catalogue.insert(con, "records", records)
    catalogue.insert(con, "tags", tags)
    con.close()
    return db


@pytest.fixture
def con(toy_db: Path):
    c = duckdb.connect(str(toy_db), read_only=True)
    yield c
    c.close()


# --- count_by -----------------------------------------------------------------------


def test_one_dimension(con):
    m = count_matrix(con, IA)
    assert m.total_records == 24
    assert m.untagged_records == 1  # u01
    assert m.row_totals == {"A": 12, "B": 11}  # a01-a10, c01, u02 / b01-b10, old
    assert m.col_totals == {} and m.col_dimension is None
    a = next(c for c in m.cells if c.row == "A")
    assert a.col is None and a.count == 12 and a.record_ids == sorted(a.record_ids)
    assert "u02" in a.record_ids


def test_two_dimensions_count_records_tagged_on_both(con):
    m = count_matrix(con, IA, ME)
    # u01 (nothing) and u02 (no method) cannot appear in any cell.
    assert (m.total_records, m.untagged_records) == (24, 2)
    cells = {(c.row, c.col): c.count for c in m.cells}
    assert cells == {("A", "x"): 10, ("B", "y"): 10, ("A", "y"): 1, ("B", "x"): 1}
    assert m.row_totals == {"A": 11, "B": 11}
    assert m.col_totals == {"x": 11, "y": 11}
    assert sum(m.row_totals.values()) == m.total_records - m.untagged_records


def test_low_confidence_tags_do_not_count(con):
    m = count_matrix(con, ME)
    y = next(c for c in m.cells if c.row == "y")
    assert "a01" not in y.record_ids
    lenient = count_matrix(con, ME, filters=Filters(min_confidence=0.2))
    y = next(c for c in lenient.cells if c.row == "y")
    assert "a01" in y.record_ids


def test_filters_narrow_the_denominator(con):
    m = count_matrix(con, IA, filters=Filters(year_from=2000))
    assert m.total_records == 23 and m.row_totals["B"] == 10
    assert m.filters.year_from == 2000
    empty = count_matrix(con, IA, filters=Filters(year_from=3000))
    assert (empty.total_records, empty.cells, empty.untagged_records) == (0, [], 0)


def test_same_dimension_twice_gives_co_occurrence(con):
    m = count_matrix(con, IA, IA)
    assert {(c.row, c.col) for c in m.cells} == {("A", "A"), ("B", "B")}


def test_needs_tags(toy_db: Path, tmp_path: Path):
    c = duckdb.connect(str(toy_db))
    c.execute("DROP TABLE tags")
    with pytest.raises(ToolError, match="no tags yet"):
        count_matrix(c, IA)
    with pytest.raises(ToolError, match="need tags"):
        count_matrix(c, IA, filters=Filters(methods=["x"]))
    c.close()


# --- find_gaps ----------------------------------------------------------------------


def test_gap_basis_leaves_out_conference_items(con):
    b = gap_basis(con, None, ["conference_item"])
    assert b.excluded_types == ["conference_item"] and b.excluded_records == 1
    assert "conference_item" not in b.filters.pub_types
    assert "article" in b.filters.pub_types
    # Types named by the user are kept as they are.
    chosen = gap_basis(con, Filters(pub_types=["conference_item"]), ["conference_item"])
    assert (chosen.filters.pub_types, chosen.excluded_records) == (
        ["conference_item"],
        0,
    )
    assert gap_basis(con, None, []).excluded_records == 0


def test_gap_cells_by_hand(con):
    b = gap_basis(con, Filters(year_from=2000), ["conference_item"])
    m = count_matrix(con, IA, ME, b.filters)
    assert m.total_records - m.untagged_records == 20
    gaps = gap_cells(m, max_ratio=0.25, min_expected=5)
    assert [(g.row, g.col, g.count, g.expected, g.ratio) for g in gaps] == [
        ("A", "y", 0, 5.0, 0.0),
        ("B", "x", 0, 5.0, 0.0),
    ]
    assert all(g.record_ids == [] for g in gaps)
    # Cells expected to hold fewer records than min_expected are not judged.
    assert gap_cells(m, max_ratio=0.25, min_expected=6) == []
    # A ratio of 1 also lists the well-covered cells' neighbours, never above 1.
    assert all(g.ratio <= 1 for g in gap_cells(m, max_ratio=1, min_expected=0))


def test_find_gaps_uses_config_and_says_what_it_left_out(toy_db: Path, tmp_path: Path):
    with use_context(ToolContext(config(tmp_path, toy_db))):
        gaps = core.find_gaps(IA, ME, Filters(year_from=2000))
        assert [(g.row, g.col) for g in gaps] == [("A", "y"), ("B", "x")]
        report = gap_report(
            ToolContext(config(tmp_path, toy_db)), IA, ME, Filters(year_from=2000)
        )
        assert report.basis.excluded_records == 1 and report.min_expected == 5
    # Keeping conference items: A-y now has c01 (1 of 5.76 expected), B-x falls
    # below min_expected (10 x 10 / 21 = 4.76) and is not judged.
    ctx = ToolContext(config(tmp_path, toy_db))
    kept = gap_report(ctx, IA, ME, Filters(year_from=2000), exclude_types=[])
    assert [(g.row, g.col, g.count) for g in kept.gaps] == [("A", "y", 1)]
    assert kept.gaps[0].record_ids == ["c01"]
    assert kept.gaps[0].expected == pytest.approx(5.76, abs=0.01)


def test_find_gaps_checks_arguments(toy_db: Path, tmp_path: Path):
    with use_context(ToolContext(config(tmp_path, toy_db))):
        with pytest.raises(ToolError, match="two different"):
            core.find_gaps(IA, IA)
        with pytest.raises(ToolError, match="max_ratio"):
            core.find_gaps(IA, ME, max_ratio=-1)


def test_min_expected_from_config(toy_db: Path, tmp_path: Path):
    cfg = config(tmp_path, toy_db, "gaps:\n  min_expected: 50\n")
    with use_context(ToolContext(cfg)):
        assert core.find_gaps(IA, ME) == []


# --- list_vocabulary ----------------------------------------------------------------


def test_dimension_entries_shapes():
    vocab = {
        "setting": ["Mountains", "Urban"],
        "sub_area": [{"code": "SL-d", "label": "Catalysts", "scope": "Tenure"}],
        "pathway": [{"code": "P1", "label": "Decisions", "rule": "Evidence"}],
        "impact_area": [{"code": "SL", "label": "Land", "boris_unit": "SLS"}],
        "method": {"geospatial_land": ["Remote sensing"]},
        "actors": {"roles": ["studied"], "groups": {"justice_groups": ["Women"]}},
        "country": {"standard": "ISO", "extra_flag": "global_or_multi_country"},
        "region": {
            "cde_partner_region": {
                "CDE-EAF": {"label": "Eastern Africa", "m49": "014"},
                "CDE-MSEA": {"label": "SE Asia", "countries": ["LAO", "MMR"]},
            }
        },
        "output_type": {"boris_type": "from metadata", "audience": ["Academic"]},
    }
    E = Entry
    assert dimension_entries(vocab, Dimension.SETTING) == [
        E("Mountains", "Mountains"),
        E("Urban", "Urban"),
    ]
    assert dimension_entries(vocab, Dimension.SUB_AREA) == [
        E("SL-d", "Catalysts", "Tenure")
    ]
    assert dimension_entries(vocab, Dimension.PATHWAY)[0].definition == "Evidence"
    assert dimension_entries(vocab, Dimension.IMPACT_AREA)[0].definition == (
        "BORIS unit: SLS"
    )
    assert dimension_entries(vocab, ME) == [
        E("Remote sensing", "Remote sensing", "Group: Geospatial land")
    ]
    assert [e.code for e in dimension_entries(vocab, Dimension.ACTORS)] == ["Women"]
    assert [e.code for e in dimension_entries(vocab, Dimension.COUNTRY)] == [
        "global_or_multi_country"
    ]
    regions = dimension_entries(vocab, Dimension.REGION)
    assert [e.code for e in regions] == ["CDE-EAF", "CDE-MSEA"]
    assert "014" in (regions[0].definition or "")
    assert "LAO, MMR" in (regions[1].definition or "")
    assert [e.code for e in dimension_entries(vocab, Dimension.OUTPUT_TYPE)] == [
        "Academic"
    ]
    sdgs = dimension_entries(vocab, Dimension.SDG)
    assert len(sdgs) == 17 and sdgs[14] == E("15", "SDG 15: Life on land")
    assert dimension_entries({}, Dimension.SCALE) == []


@pytest.fixture
def fixture_ctx(fixture_db: Path, tmp_path: Path):
    with use_context(ToolContext(config(tmp_path, fixture_db))) as c:
        yield c


def _sql_counts(db: Path, dim: str) -> dict[str, int]:
    c = duckdb.connect(str(db), read_only=True)
    rows = c.execute(
        "SELECT term, count(DISTINCT record_id) FROM tags "
        "WHERE dimension = ? AND confidence >= 0.5 GROUP BY term",
        [dim],
    ).fetchall()
    c.close()
    return dict(rows)


def test_list_vocabulary_on_the_fixtures(fixture_ctx, fixture_db: Path):
    terms = core.list_vocabulary(Dimension.SUB_AREA)
    assert [t.code for t in terms][:3] == ["JE-a", "JE-b", "JE-c"]
    assert len(terms) == 16 and all(t.definition for t in terms)
    expected = _sql_counts(fixture_db, "sub_area")
    assert {t.code: t.record_count for t in terms if t.record_count} == expected

    methods = core.list_vocabulary(ME)
    # vocabulary v0.3: 60 coded method terms (decision 0025)
    assert len([t for t in methods if t.code.startswith("MET-")]) == 60
    other = next(t for t in methods if t.code == "other")
    assert other.label == "Other" and other.record_count > 0

    countries = core.list_vocabulary(Dimension.COUNTRY)
    che = next(t for t in countries if t.code == "CHE")
    assert che.record_count == _sql_counts(fixture_db, "country")["CHE"]
    assert "ISO" in (che.definition or "")


def test_list_vocabulary_errors(fixture_db: Path, tmp_path: Path):
    cfg = config(tmp_path, fixture_db, 'vocab:\n  file: "missing.yaml"\n')
    with use_context(ToolContext(cfg)):
        with pytest.raises(ToolError, match="no vocabulary file"):
            core.list_vocabulary(IA)
    db = Path(shutil.copy(fixture_db, tmp_path / "c.duckdb"))
    c = duckdb.connect(str(db))
    c.execute("DROP TABLE tags")
    c.close()
    with use_context(ToolContext(config(tmp_path, db))):
        terms = core.list_vocabulary(IA)
    assert len(terms) == 4 and all(t.record_count == 0 for t in terms)


def test_dimension_overview(fixture_ctx):
    summaries, total = dimension_overview(fixture_ctx)
    assert [s.dimension for s in summaries] == list(Dimension)
    assert total == 34
    country = next(s for s in summaries if s.dimension is Dimension.COUNTRY)
    assert country.records_tagged == 34 and country.terms_used == country.terms


# --- CLI ----------------------------------------------------------------------------


@pytest.fixture
def cli(toy_db: Path, tmp_path: Path):
    override = make_config(tmp_path, toy_db)
    runner = CliRunner(env={"COLUMNS": "200"})
    return lambda *args: runner.invoke(app, ["--config", str(override), *args])


def test_cli_count(cli):
    result = cli("count", "impact_area")
    assert result.exit_code == 0, result.output
    assert "24 matching records; 1 have no tag" in result.output
    result = cli("count", "impact_area", "method", "--year-from", "2000")
    assert result.exit_code == 0, result.output
    assert "21 records are tagged on both" in result.output
    data = json.loads(cli("count", "impact_area", "method", "--json").stdout)
    assert data["total_records"] == 24 and data["untagged_records"] == 2


def test_cli_gaps(cli):
    result = cli("count", "impact_area", "method", "--gaps", "--year-from", "2000")
    assert result.exit_code == 0, result.output
    assert "Left out: 1 records of type conference_item" in result.output
    assert "20 tagged on both" in result.output
    data = json.loads(
        cli("count", "impact_area", "method", "--gaps", "--json", "--year-from", "2000")
        .stdout
    )  # fmt: skip
    assert [(g["row"], g["col"]) for g in data] == [("A", "y"), ("B", "x")]
    kept = cli("count", "impact_area", "method", "--gaps", "--all-types")
    assert "Left out" not in kept.output
    assert cli("count", "impact_area", "--gaps").exit_code == 2


def test_cli_vocab_and_schema(fixture_db: Path, tmp_path: Path):
    override = make_config(tmp_path, fixture_db)
    runner = CliRunner(env={"COLUMNS": "200"})

    def run(*args: str):
        return runner.invoke(app, ["--config", str(override), *args])

    result = run("vocab", "-d", "region")
    assert result.exit_code == 0, result.output
    assert "CDE-EAF" in result.output and "Eastern Africa" in result.output
    data = json.loads(run("vocab", "-d", "sdg", "--json").stdout)
    assert len(data) == 17 and data[0]["code"] == "1"
    for args in (["vocab"], ["schema"]):
        result = run(*args)
        assert result.exit_code == 0, result.output
        assert "transformative_aspiration" in result.output and "of 34" in result.output
    assert run("vocab", "--json").exit_code == 2
