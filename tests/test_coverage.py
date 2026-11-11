"""Tests for ``coverage_report`` and ``chatcde coverage`` (issues #11, #43).

Expected numbers are recomputed from the fixture JSONL files, independently of
the SQL in :mod:`chatcde.tools.coverage`.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from chatcde import catalogue
from chatcde.cli import app
from chatcde.config import load_config
from chatcde.schemas import CoverageReport, Edition, Filters, Record
from chatcde.store.filters import FilterError
from chatcde.tools import core
from chatcde.tools import coverage as cov
from chatcde.tools.context import ToolContext, use_context

FIX = Path(__file__).parent / "fixtures"
runner = CliRunner()


def jsonl(name: str) -> list[dict[str, Any]]:
    lines = (FIX / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def expected_status() -> dict[str, str]:
    """record_id → text status, from records.jsonl and pages.jsonl."""
    with_text = {p["record_id"] for p in jsonl("pages.jsonl") if p["text"].strip()}
    out = {}
    for r in jsonl("records.jsonl"):
        if r["record_id"] in with_text:
            out[r["record_id"]] = "fulltext"
        elif r["abstract"] is not None:
            out[r["record_id"]] = "abstract_only"
        else:
            out[r["record_id"]] = "metadata_only"
    return out


@pytest.fixture
def con(fixture_db: Path) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(str(fixture_db), read_only=True)
    yield c
    c.close()


def test_report_matches_fixture_files(con: duckdb.DuckDBPyConnection):
    records = jsonl("records.jsonl")
    status = Counter(expected_status().values())
    r = cov.build_report(con, None, Edition.FULL)

    assert r.total_records == len(records) == 34
    assert r.with_fulltext == status["fulltext"]
    assert r.abstract_only == status["abstract_only"]
    assert r.metadata_only == status["metadata_only"]
    assert r.with_fulltext + r.abstract_only + r.metadata_only == r.total_records
    assert r.by_type == dict(
        sorted(
            Counter(x["pub_type"] for x in records).items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
    )
    assert r.by_access == Counter(x["access"] for x in records)
    assert r.by_language == Counter(
        x["languages"][0].lower() if x["languages"] else "none" for x in records
    )
    years = Counter(str(x["year"]) if x["year"] else "unknown" for x in records)
    assert r.by_year == years
    assert list(r.by_year)[-1] == "unknown"  # synthetic-03 has no year
    assert list(r.by_year)[:-1] == sorted(k for k in years if k != "unknown")


def test_low_confidence_tags_follow_the_threshold(con: duckdb.DuckDBPyConnection):
    tags = jsonl("tags.jsonl")
    assert cov.low_confidence_tags(con, None) == sum(
        t["confidence"] < 0.5 for t in tags
    )
    loose = Filters(min_confidence=0.31)
    assert cov.low_confidence_tags(con, loose) == sum(
        t["confidence"] < 0.31 for t in tags
    )
    assert cov.low_confidence_tags(con, loose) > 0  # synthetic tags at 0.3


@pytest.mark.parametrize("by", list(cov.BREAKDOWNS))
def test_every_breakdown_adds_up_to_the_total(
    con: duckdb.DuckDBPyConnection, by: cov.Breakdown
):
    split = cov.breakdown(con, None, by)
    totals: Counter[str] = Counter()
    for c in split.values():
        assert set(c) == set(cov.STATUSES)
        totals.update(c)
    assert totals == Counter(expected_status().values())


def test_filters_restrict_the_denominator(con: duckdb.DuckDBPyConnection):
    articles = [r for r in jsonl("records.jsonl") if r["pub_type"] == "article"]
    r = cov.build_report(con, Filters(pub_types=["Article"]), Edition.FULL)
    assert r.total_records == len(articles)
    assert r.by_type == {"article": len(articles)}
    assert r.filters.pub_types == ["Article"]

    none = cov.build_report(con, Filters(year_from=3000), Edition.FULL)
    assert none.total_records == 0 and none.by_year == {}


def test_tag_filter_without_tags_fails(tmp_path: Path):
    db = tmp_path / "cat.duckdb"
    c = catalogue.connect(db)
    catalogue.replace_tables(c, {"records": [record("r1")]})
    with pytest.raises(FilterError):
        cov.build_report(c, Filters(countries=["KEN"]), Edition.FULL)
    c.close()


def record(rid: str, **kw: Any) -> Record:
    return Record(
        record_id=rid,
        title=f"Title {rid}",
        pub_type="article",
        boris_url=f"https://boris-portal.unibe.ch/entities/publication/{rid}",
        harvested_at=datetime(2026, 11, 7, tzinfo=UTC),
        **kw,
    )


def test_downloads_stand_in_until_pages_exist(tmp_path: Path):
    db = tmp_path / "cat.duckdb"
    c = catalogue.connect(db)
    catalogue.replace_tables(
        c,
        {
            "records": [
                record("r1", has_fulltext=True),
                record("r2", abstract="An abstract."),
                record("r3"),
            ]
        },
    )
    assert cov.fulltext_basis(c) == "downloads"
    r = cov.build_report(c, None, Edition.FULL)
    assert (r.with_fulltext, r.abstract_only, r.metadata_only) == (1, 1, 1)
    assert r.low_confidence_tags == 0  # no tags table
    c.close()


def test_fixture_basis_is_pages(con: duckdb.DuckDBPyConnection):
    assert cov.fulltext_basis(con) == "pages"


def test_local_edition_reads_the_bundle_manifest(tmp_path: Path):
    assert cov.local_edition(tmp_path) == Edition.FULL
    manifest = {
        "edition": "open",
        "version": "2026.11.0",
        "built_at": "2026-11-07T00:00:00Z",
        "code_version": "abc",
        "vocab_version": "0.2",
        "models": {},
        "record_count": 1,
        "fulltext_count": 0,
        "files": {},
    }
    (tmp_path / cov.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    assert cov.local_edition(tmp_path) == Edition.OPEN


def config_for(tmp_path: Path, db: Path) -> Path:
    path = tmp_path / "cov.yaml"
    path.write_text(
        f'data:\n  dir: "{tmp_path.as_posix()}"\n  catalogue: "{db.as_posix()}"\n'
        "logging:\n  file: null\n",
        encoding="utf-8",
    )
    return path


def test_tool_reads_the_context_catalogue(fixture_db: Path, tmp_path: Path):
    # It used to load the default config, ignoring the agent's context (#66).
    cfg = load_config(config_for(tmp_path, fixture_db))
    with use_context(ToolContext(cfg)):
        r = core.coverage_report(Filters(languages=["de"]))
    assert r.edition == Edition.FULL
    de = [r for r in jsonl("records.jsonl") if "de" in r["languages"]]
    assert r.total_records == len(de) > 0


def test_cli_json_is_the_report(fixture_db: Path, tmp_path: Path):
    cfg = config_for(tmp_path, fixture_db)
    result = runner.invoke(app, ["--config", str(cfg), "coverage", "--json"])
    assert result.exit_code == 0, result.output
    report = CoverageReport.model_validate_json(result.output)
    assert report.total_records == 34


def test_cli_tables_and_errors(fixture_db: Path, tmp_path: Path):
    cfg = config_for(tmp_path, fixture_db)
    result = runner.invoke(
        app, ["--config", str(cfg), "coverage", "--by", "access", "--type", "report"]
    )
    assert result.exit_code == 0, result.output
    assert "By access" in result.output and "By year" not in result.output

    bad = runner.invoke(app, ["--config", str(cfg), "coverage", "--by", "colour"])
    assert bad.exit_code == 2 and "--by must be one of" in bad.output

    (tmp_path / "x").mkdir()
    missing = config_for(tmp_path / "x", tmp_path / "none.duckdb")
    gone = runner.invoke(app, ["--config", str(missing), "coverage"])
    assert gone.exit_code == 1 and "No catalogue" in gone.output
