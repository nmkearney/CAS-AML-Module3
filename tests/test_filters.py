"""Filters: every tagged dimension, no unknown fields, no unknown codes (#65, 0034)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import typer
from pydantic import ValidationError

from chatcde.cli_common import build_filters
from chatcde.config import Config, load_config
from chatcde.schemas import Dimension, Filters
from chatcde.store.filters import TAG_FIELDS
from chatcde.tools import core
from chatcde.tools.context import ToolContext, ToolError, use_context
from chatcde.tools.specs import run_tool, tool_specs
from chatcde.tools.vocabulary import WorldCodes, check_filter_codes, load_vocabulary

#: Filters fields answered from the ``records`` table, not from tags.
RECORD_FIELDS = {"year_from", "year_to", "cde_units", "pub_types", "languages"}


def _config(tmp_path: Path, db: Path) -> Path:
    path = tmp_path / "o.yaml"
    path.write_text(
        f'data:\n  catalogue: "{db.as_posix()}"\nlogging:\n  file: null\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def cfg(tmp_path: Path, fixture_db: Path) -> Config:
    return load_config(_config(tmp_path, fixture_db))


@pytest.fixture
def vocab(cfg: Config) -> dict:
    return load_vocabulary(cfg.vocab.file)


@pytest.fixture
def world(cfg: Config) -> WorldCodes:
    return WorldCodes.from_m49(cfg.tagging.m49_file)


def _records_with(db: Path, dim: str, term: str) -> int:
    con = duckdb.connect(str(db), read_only=True)
    row = con.execute(
        "SELECT count(DISTINCT record_id) FROM tags "
        "WHERE dimension = ? AND term = ? AND confidence >= 0.5",
        [dim, term],
    ).fetchone()
    con.close()
    return int(row[0]) if row else 0


# --- the model ---------------------------------------------------------------------


def test_unknown_fields_are_rejected():
    # The agent wrote impact_area (no s) on 2026-11-11; it used to be dropped.
    with pytest.raises(ValidationError, match="Extra inputs"):
        Filters.model_validate({"impact_area": ["SG"]})


def test_every_tagged_dimension_has_a_filter_field():
    assert set(TAG_FIELDS.values()) == set(Dimension)
    list_fields = set(Filters.model_fields) - {"min_confidence"} - RECORD_FIELDS
    assert set(TAG_FIELDS) == list_fields


def test_tool_definition_forbids_extra_filter_keys():
    params = tool_specs(["count_by"])["count_by"].parameters()
    node = params["properties"]["filters"]
    node = node["anyOf"][0] if "anyOf" in node else node
    assert node["additionalProperties"] is False
    assert "impact_areas" in node["properties"]


def test_run_tool_reports_an_unknown_filter_key_to_the_model(cfg: Config):
    with use_context(ToolContext(cfg)):
        out = run_tool(
            tool_specs(["count_by"])["count_by"],
            {"row": "method", "filters": {"impact_area": ["SG"]}},
            max_chars=500,
        )
    assert not out.ok
    assert "filters.impact_area" in (out.error or "")


# --- new filters reach the counts --------------------------------------------------


def test_impact_area_filter_narrows_count_by(cfg: Config, fixture_db: Path):
    expected = _records_with(fixture_db, "impact_area", "SL")
    assert 0 < expected
    with use_context(ToolContext(cfg)):
        everything = core.count_by(Dimension.METHOD)
        sl = core.count_by(Dimension.METHOD, filters=Filters(impact_areas=["SL"]))
    assert sl.total_records == expected < everything.total_records


def test_coverage_report_honours_tag_filters(cfg: Config, fixture_db: Path):
    with use_context(ToolContext(cfg)):
        report = core.coverage_report(Filters(impact_areas=["sl"]))  # case ignored
    assert report.total_records == _records_with(fixture_db, "impact_area", "SL")


# --- unknown codes -----------------------------------------------------------------


def test_known_codes_pass(vocab: dict, world: WorldCodes):
    check_filter_codes(
        Filters(
            impact_areas=["SG", "te"],
            methods=["MET-G-biophysical", "MET-case-study", "other"],
            countries=["KEN", "global_or_multi_country"],
            regions=["CDE-EAF", "Eastern Africa", "cde_partner_country"],
            settings=["SET-mountains"],
            sdgs=[15],
            output_types=["Policy"],
        ),
        vocab,
        world,
    )


def test_a_label_names_its_code(vocab: dict, world: WorldCodes):
    with pytest.raises(ToolError, match="'mountains' is a label; use the code 'SET-"):
        check_filter_codes(Filters(settings=["mountains"]), vocab, world)


def test_a_country_name_names_its_code(vocab: dict, world: WorldCodes):
    with pytest.raises(ToolError, match="use the code 'KEN'"):
        check_filter_codes(Filters(countries=["Kenya"]), vocab, world)


def test_a_retired_code_names_its_replacement(vocab: dict, world: WorldCodes):
    with pytest.raises(ToolError, match="retired; use 'DAT-visual-audio-data'"):
        check_filter_codes(Filters(data_types=["DAT-visual-data"]), vocab, world)


def test_an_unknown_code_gets_suggestions_and_the_short_list(
    vocab: dict, world: WorldCodes
):
    with pytest.raises(ToolError) as err:
        check_filter_codes(
            Filters(impact_areas=["Sustainable Governance"]), vocab, world
        )
    message = str(err.value)
    assert "did you mean 'SG'" in message
    assert "codes: JE, SG, SL, TE" in message


def test_every_bad_field_is_reported_at_once(vocab: dict, world: WorldCodes):
    with pytest.raises(ToolError) as err:
        check_filter_codes(
            Filters(methods=["MET-nope"], sdgs=[18], countries=["XXX"]), vocab, world
        )
    for field in ("filters.methods", "filters.sdgs", "filters.countries"):
        assert field in str(err.value)


def test_countries_are_not_checked_without_the_m49_table(vocab: dict):
    check_filter_codes(Filters(countries=["XXX"]), vocab, None)


def test_tools_check_codes_before_counting(cfg: Config):
    with use_context(ToolContext(cfg)), pytest.raises(ToolError, match="impact_area"):
        core.count_by(Dimension.METHOD, filters=Filters(impact_areas=["Governance"]))


# --- --tag on the command line -----------------------------------------------------


def test_build_filters_adds_tags_to_the_options():
    f = build_filters(
        ["impact_area:SG", "impact_area:TE", "sdg:15", "country:LAO"],
        countries=["KHM"],
        year_from=2010,
        year_to=None,
    )
    assert f.impact_areas == ["SG", "TE"]
    assert f.sdgs == [15]
    assert f.countries == ["KHM", "LAO"]
    assert f.year_from == 2010


@pytest.mark.parametrize("bad", ["impact_area", "nope:SG", "impact_area:", "sdg:x"])
def test_build_filters_rejects_a_malformed_tag(bad: str):
    with pytest.raises(typer.Exit) as exc:
        build_filters([bad])
    assert exc.value.exit_code == 2
