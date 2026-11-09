"""Tests for the group level of counts, gaps, vocabulary and filters (#57, #58).

A tiny vocabulary with two method groups and a checkerboard catalogue make every
count easy to work out by hand; the real vocabulary and the fixtures check that
v0.3's groups are read as agreed. No network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chatcde import catalogue
from chatcde.cli import app
from chatcde.config import load_config
from chatcde.schemas import Dimension, Filters
from chatcde.tools.context import ToolContext, ToolError
from chatcde.tools.counts import count_by, find_gaps, list_vocabulary
from chatcde.tools.vocabulary import (
    expand_filters,
    group_entries,
    group_map,
    load_vocabulary,
)
from chatcde.workflows.gaps import run_gaps
from test_tools_counts import rec, tag

SA, ME = Dimension.SUB_AREA, Dimension.METHOD

VOCAB = """\
version: test
sub_area:
  - {code: S1, label: Sub one}
  - {code: S2, label: Sub two}
method:
  groups:
    - {code: G-a, label: Group A}
    - {code: G-b, label: Group B}
  terms:
    - {code: M-a1, label: A one, group: G-a}
    - {code: M-a2, label: A two, group: G-a}
    - {code: M-b1, label: B one, group: G-b}
"""


@pytest.fixture
def grouped(tmp_path: Path) -> ToolContext:
    """20 records: s01-s10 are S1 with M-a1 and M-a2 (both in G-a); t01-t10 are
    S2 with M-b1 (G-b). t01 also has `other`. So S1 x G-b and S2 x G-a are gaps,
    each expected 10 x 10 / 20 = 5.
    """
    records = [rec(f"s{i:02}") for i in range(1, 11)]
    records += [rec(f"t{i:02}") for i in range(1, 11)]
    tags = []
    for i in range(1, 11):
        tags += [
            tag(f"s{i:02}", SA, "S1"),
            tag(f"s{i:02}", ME, "M-a1"),
            tag(f"s{i:02}", ME, "M-a2"),
            tag(f"t{i:02}", SA, "S2"),
            tag(f"t{i:02}", ME, "M-b1"),
        ]
    tags.append(tag("t01", ME, "other"))
    db = tmp_path / "g.duckdb"
    con = catalogue.connect(db)
    catalogue.ensure_tables(con, ["records", "tags"])
    catalogue.insert(con, "records", records)
    catalogue.insert(con, "tags", tags)
    con.close()
    vocab = tmp_path / "vocab.yaml"
    vocab.write_text(VOCAB, encoding="utf-8")
    override = tmp_path / "o.yaml"
    override.write_text(
        f'data:\n  catalogue: "{db.as_posix()}"\n'
        f'vocab:\n  file: "{vocab.as_posix()}"\nlogging:\n  file: null\n',
        encoding="utf-8",
    )
    return ToolContext(load_config(override))


# --- the real vocabulary --------------------------------------------------------------


def test_groups_of_the_real_vocabulary():
    vocab = load_vocabulary(load_config().vocab.file)
    grouped = {d for d in Dimension if group_map(vocab, d)}
    assert grouped == {
        Dimension.METHOD, Dimension.DATA_TYPE, Dimension.SETTING,
        Dimension.SYSTEM, Dimension.ACTORS, Dimension.ADDITIONAL_SUB_AREA,
    }  # fmt: skip
    methods = group_entries(vocab, ME)
    assert len(methods) == 8 and all(e.code.startswith("MET-G-") for e in methods)
    asa = group_map(vocab, Dimension.ADDITIONAL_SUB_AREA) or {}
    assert set(asa.values()) <= {"JE", "SL", "SG", "TE", "cross_cutting", "outside"}
    labels = {
        e.code: e.label for e in group_entries(vocab, Dimension.ADDITIONAL_SUB_AREA)
    }
    assert labels["cross_cutting"] == "Cross-cutting" and labels["SL"] != "SL"


def test_expand_filters():
    vocab = load_vocabulary(load_config().vocab.file)
    f = expand_filters(Filters(methods=["MET-G-biophysical", "MET-x"]), vocab)
    assert f is not None and "MET-x" in f.methods and len(f.methods) > 2
    assert "MET-G-biophysical" not in f.methods
    plain = Filters(methods=["MET-field-survey"], countries=["LAO"])
    assert expand_filters(plain, vocab) is plain
    assert expand_filters(None, vocab) is None


# --- counts ---------------------------------------------------------------------------


def test_count_by_group_counts_each_record_once(grouped: ToolContext):
    m = count_by(grouped, ME)
    assert m.row_level == "group"
    # s01-s10 have two G-a terms each but count once; t01's `other` stays.
    assert m.row_totals == {"G-a": 10, "G-b": 10, "other": 1}
    t = count_by(grouped, ME, level="term")
    assert t.row_level == "term"
    assert t.row_totals == {"M-a1": 10, "M-a2": 10, "M-b1": 10, "other": 1}


def test_two_dimensions_and_levels(grouped: ToolContext):
    m = count_by(grouped, SA, ME)
    assert (m.row_level, m.col_level) == ("term", "group")  # sub_area has no groups
    cells = {(c.row, c.col): c.count for c in m.cells}
    assert cells == {("S1", "G-a"): 10, ("S2", "G-b"): 10, ("S2", "other"): 1}


def test_group_codes_in_filters(grouped: ToolContext):
    m = count_by(grouped, SA, filters=Filters(methods=["G-b"]))
    assert m.total_records == 10 and m.row_totals == {"S2": 10}


def test_find_gaps_by_group(grouped: ToolContext):
    gaps = find_gaps(grouped, SA, ME)
    assert {(g.row, g.col) for g in gaps} == {("S1", "G-b"), ("S2", "G-a")}
    assert all(g.expected == 5.0 and g.count == 0 for g in gaps)
    with pytest.raises(ToolError, match="level"):
        find_gaps(grouped, SA, ME, level="detail")  # type: ignore[arg-type]


def test_list_vocabulary_levels(grouped: ToolContext):
    terms = list_vocabulary(grouped, ME)
    assert [(t.code, t.group, t.record_count) for t in terms] == [
        ("M-a1", "G-a", 10), ("M-a2", "G-a", 10), ("M-b1", "G-b", 10),
        ("other", None, 1),
    ]  # fmt: skip
    groups = list_vocabulary(grouped, ME, level="group")
    assert [(g.code, g.label, g.record_count) for g in groups] == [
        ("G-a", "Group A", 10), ("G-b", "Group B", 10), ("other", "Other", 1),
    ]  # fmt: skip
    assert groups[0].definition == "Group of 2 terms" and groups[0].group is None
    # A dimension without groups is listed by term at either level.
    assert [t.code for t in list_vocabulary(grouped, SA, level="group")] == ["S1", "S2"]


# --- chatcde gaps ---------------------------------------------------------------------


def test_gaps_workflow_lists_terms_inside_group_cells(grouped: ToolContext):
    r = run_gaps(grouped, SA, ME, use_llm=False, search=lambda *a: [])
    assert (r.row_level, r.col_level, r.level) == ("term", "group", "group")
    cell = next(c for c in r.cells if c.col == "G-a")
    assert cell.col_label == "Group A"
    assert [(b.code, b.count) for b in cell.col_breakdown] == [
        ("M-a1", 10),
        ("M-a2", 10),
    ]
    assert cell.row_breakdown == []  # sub_area is not grouped
    t = run_gaps(grouped, SA, ME, use_llm=False, level="term", search=lambda *a: [])
    assert t.col_level == "term" and all(not c.col_breakdown for c in t.cells)


# --- CLI on the fixtures --------------------------------------------------------------


def _cli(fixture_db: Path, tmp_path: Path, *args: str):  # type: ignore[no-untyped-def]
    override = tmp_path / "o.yaml"
    override.write_text(
        f'data:\n  catalogue: "{fixture_db.as_posix()}"\nlogging:\n  file: null\n',
        encoding="utf-8",
    )
    runner = CliRunner(env={"COLUMNS": "250"})
    return runner.invoke(app, ["--config", str(override), *args])


def test_cli_count_levels_on_fixtures(fixture_db: Path, tmp_path: Path):
    res = _cli(fixture_db, tmp_path, "count", "method", "--json")
    assert res.exit_code == 0, res.output
    rows = json.loads(res.stdout)["row_totals"]
    assert rows and all(c.startswith("MET-G-") or c == "other" for c in rows)
    res = _cli(fixture_db, tmp_path, "count", "method", "--level", "term", "--json")
    rows = json.loads(res.stdout)["row_totals"]
    assert any(c.startswith("MET-") and not c.startswith("MET-G-") for c in rows)
    res = _cli(fixture_db, tmp_path, "count", "method")
    assert "Counted by: method group" in res.stdout
    assert _cli(fixture_db, tmp_path, "count", "method", "--level", "x").exit_code == 2


def test_cli_vocab_levels(fixture_db: Path, tmp_path: Path):
    res = _cli(fixture_db, tmp_path, "vocab", "-d", "method")
    assert res.exit_code == 0, res.output
    assert "Group" in res.stdout and "MET-G-biophysical" in res.stdout
    res = _cli(
        fixture_db, tmp_path, "vocab", "-d", "method", "--level", "group", "--json"
    )
    codes = [t["code"] for t in json.loads(res.stdout)]
    assert codes[:8] == sorted(codes[:8]) and all(
        c.startswith("MET-G-") for c in codes[:8]
    )


def test_tool_definitions_offer_the_level():
    from chatcde.tools.specs import tool_specs

    specs = tool_specs(["count_by", "find_gaps", "list_vocabulary"])
    for name, default in (("count_by", "group"), ("find_gaps", "group"),
                          ("list_vocabulary", "term")):  # fmt: skip
        level = specs[name].parameters()["properties"]["level"]
        assert level["enum"] == ["group", "term"] and level["default"] == default
        assert "group" in specs[name].definition()["function"]["description"]
