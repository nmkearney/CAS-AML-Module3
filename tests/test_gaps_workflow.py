"""Tests for the ``chatcde gaps`` workflow (issue #27). No network.

Counts run on the checkerboard catalogue of ``test_tools_counts.py`` (impact
areas A/B x methods x/y, where A-y and B-x are gaps). The search is replaced
by a fake that returns fixed passages; the model is the scripted fake from
``test_agent.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.config import load_config
from chatcde.schemas import Chunk, Citation, Dimension, Filters, Passage, TextSource
from chatcde.store.endpoint import EndpointClient
from chatcde.tools.context import ToolContext, ToolError
from chatcde.workflows import gaps as gw
from chatcde.workflows.gaps import (
    GapsError,
    judgement_schema,
    parse_dims,
    render_gaps,
    run_gaps,
)
from test_agent import ScriptedLLM, reply
from test_tools_counts import make_config, toy_db  # noqa: F401  (fixture)

IA, ME = Dimension.IMPACT_AREA, Dimension.METHOD
#: Leaves out the 1990 record, so both gap cells expect 10 x 10 / 20 = 5 records.
SCOPE = Filters(year_from=2000)


def passage(rid: str, text: str = "A study on A with y.") -> Passage:
    return Passage(
        chunk=Chunk(
            chunk_id=f"{rid}:0",
            record_id=rid,
            text=text,
            token_count=8,
            source=TextSource.ABSTRACT_BORIS,
        ),
        score=0.9,
        citation=Citation(
            record_id=rid,
            title=f"Record {rid}",
            year=2020,
            doi=None,
            boris_url=f"https://boris.test/{rid}",
        ),
    )


class FakeSearch:
    """Returns a01 and b01 (twice, to check de-duplication) for every query."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.queries: list[tuple[str, Filters]] = []

    def __call__(self, ctx, query, filters, k):  # type: ignore[no-untyped-def]
        self.queries.append((query, filters))
        if self.fail:
            raise ToolError("no search index; run `chatcde index bm25`")
        return [passage("a01"), passage("a01", "again"), passage("b01")]


def context(tmp_path: Path, db: Path, llm: ScriptedLLM | None = None) -> ToolContext:
    cfg = load_config(make_config(tmp_path, db))
    client = None
    if llm is not None:
        client = EndpointClient(
            "https://llm.test/v1",
            "k",
            timeout_s=5,
            max_retries=0,
            transport=httpx.MockTransport(llm),
            sleep=lambda _: None,
        )
    return ToolContext(cfg, client)


def judgement(**over) -> dict:  # type: ignore[no-untyped-def]
    data = {
        "summary": "Two clear gaps.",
        "cells": [
            {
                "row": "A",
                "col": "y",
                "verdict": "tagging_artefact",
                "reason": "Record a01 is about both.",
                "evidence": ["a01", "made-up"],
            },
        ],
        "caveats": ["Small catalogue."],
    }
    data.update(over)
    return data


# --- pieces ---------------------------------------------------------------------------


def test_parse_dims():
    assert parse_dims("sub_area, method") == (Dimension.SUB_AREA, ME)
    for bad in ("method", "method,method", "method,nope", "a,b,c"):
        with pytest.raises(GapsError):
            parse_dims(bad)


def test_judgement_schema_is_strict():
    schema = judgement_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and '"title"' not in text

    def objects(node):  # type: ignore[no-untyped-def]
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    for obj in objects(schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    verdicts = schema["properties"]["cells"]["items"]["properties"]["verdict"]
    assert set(verdicts["enum"]) == set(gw.VERDICT_LABELS)


# --- run_gaps -------------------------------------------------------------------------


def test_counts_and_evidence_without_llm(toy_db: Path, tmp_path: Path):  # noqa: F811
    search = FakeSearch()
    ctx = context(tmp_path, toy_db)
    r = run_gaps(ctx, IA, ME, SCOPE, use_llm=False, search=search)
    assert {(c.row, c.col) for c in r.cells} == {("A", "y"), ("B", "x")}
    assert r.excluded_types == ["conference_item"] and r.excluded_records == 1
    assert r.judgement is None and r.model is None
    assert r.total_records == 22 and r.tagged_on_both == 20
    cell = r.cells[0]
    # de-duplicated, each with its own tags on both dimensions
    assert [n.citation.record_id for n in cell.nearby] == ["a01", "b01"]
    assert cell.nearby[0].row_terms == ["A"] and cell.nearby[0].col_terms == ["x"]
    # the search ran within the scope used for counting (types excluded)
    query, filters = search.queries[0]
    assert "conference_item" not in filters.pub_types and filters.pub_types
    assert any("conference_item" in c for c in r.caveats)
    assert any("2 of 22" in c for c in r.caveats)


def test_max_cells_and_all_types(toy_db: Path, tmp_path: Path):  # noqa: F811
    ctx = context(tmp_path, toy_db)
    r = run_gaps(ctx, IA, ME, SCOPE, max_cells=1, use_llm=False, search=FakeSearch())
    assert len(r.cells) == 1 and r.gaps_found == 2
    assert any("1 of 2 gaps are shown" in c for c in r.caveats)
    r = run_gaps(ctx, IA, ME, SCOPE, all_types=True, use_llm=False,
                 search=FakeSearch())  # fmt: skip
    assert r.excluded_types == [] and r.total_records == 23  # c01 counted
    assert not any("conference_item" in c for c in r.caveats)


def test_judgement_is_checked(toy_db: Path, tmp_path: Path):  # noqa: F811
    llm = ScriptedLLM([reply(json.dumps(judgement()))])
    ctx = context(tmp_path, toy_db, llm)
    r = run_gaps(ctx, IA, ME, SCOPE, search=FakeSearch())
    assert r.judgement is not None and r.judgement_error is None
    by = {(j.row, j.col): j for j in r.judgement.cells}
    assert by[("A", "y")].evidence == ["a01"]  # the made-up ID is dropped ...
    assert r.dropped_ids == ["made-up"]  # ... and reported
    assert by[("B", "x")].verdict == "unclear"  # left out by the model
    assert r.model == ctx.cfg.models.agent and r.prompt_version == "gaps.v2"
    assert r.usage["total_tokens"] == 110

    request = llm.requests[0]
    assert request["response_format"]["type"] == "json_schema"
    assert "{schema}" not in request["messages"][0]["content"]
    sent = json.loads(request["messages"][1]["content"])
    assert sent["tagged_on_both"] == 20
    assert [c["count"] for c in sent["cells"]] == [c.count for c in r.cells]
    assert sent["cells"][0]["nearby"][0]["tags_method"] == ["x"]


def test_fenced_json_is_accepted(toy_db: Path, tmp_path: Path):  # noqa: F811
    text = "```json\n" + json.dumps(judgement(cells=[])) + "\n```"
    ctx = context(tmp_path, toy_db, ScriptedLLM([reply(text)]))
    r = run_gaps(ctx, IA, ME, SCOPE, search=FakeSearch())
    assert r.judgement is not None
    assert all(j.verdict == "unclear" for j in r.judgement.cells)


def test_bad_reply_keeps_counts(toy_db: Path, tmp_path: Path):  # noqa: F811
    ctx = context(tmp_path, toy_db, ScriptedLLM([reply("Not JSON at all.")]))
    r = run_gaps(ctx, IA, ME, SCOPE, search=FakeSearch())
    assert r.judgement is None and "not a valid judgement" in (r.judgement_error or "")
    assert len(r.cells) == 2


def test_search_failure_is_a_caveat(toy_db: Path, tmp_path: Path):  # noqa: F811
    ctx = context(tmp_path, toy_db)
    r = run_gaps(ctx, IA, ME, SCOPE, use_llm=False, search=FakeSearch(fail=True))
    assert all(c.search_error and not c.nearby for c in r.cells)
    assert any("search for nearby records failed" in c for c in r.caveats)


def test_same_dimension_is_an_error(toy_db: Path, tmp_path: Path):  # noqa: F811
    with pytest.raises(GapsError):
        run_gaps(context(tmp_path, toy_db), ME, ME, use_llm=False)


def test_render(toy_db: Path, tmp_path: Path):  # noqa: F811
    llm = ScriptedLLM([reply(json.dumps(judgement()))])
    r = run_gaps(context(tmp_path, toy_db, llm), IA, ME, SCOPE, search=FakeSearch())
    console = Console(record=True, width=200)
    render_gaps(console, r)
    out = console.export_text()
    assert "Gaps: impact_area x method" in out
    assert "22 matching records; 20 tagged on both" in out
    assert "tagging artefact" in out and "Two clear gaps." in out
    assert "A x y: tagging artefact. Record a01 is about both. [1]" in out
    assert "nearby [1] impact_area: A; method: x" in out
    assert "[1] Record a01 (2020). BORIS: https://boris.test/a01" in out
    assert "Small catalogue. (model)" in out and "made-up" in out


# --- CLI ------------------------------------------------------------------------------


@pytest.fixture
def cli(toy_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """``cli(replies, *args)`` runs chatcde with the fake search and model."""
    from chatcde.store import endpoint

    override = make_config(tmp_path, toy_db)
    monkeypatch.setattr(gw, "_default_search", FakeSearch())

    def invoke(replies: list[dict], *args: str):  # type: ignore[no-untyped-def]
        llm = ScriptedLLM(replies)
        real_init = endpoint.EndpointClient.__init__

        def init_with_fake(self, *a, **kw):  # type: ignore[no-untyped-def]
            kw["transport"] = httpx.MockTransport(llm)
            kw["sleep"] = lambda _: None
            real_init(self, *a, **kw)

        monkeypatch.setattr(endpoint.EndpointClient, "__init__", init_with_fake)
        runner = CliRunner(env={"COLUMNS": "200", "CHATCDE_LLM_API_KEY": "k"})
        result = runner.invoke(app, ["--config", str(override), *args])
        monkeypatch.setattr(endpoint.EndpointClient, "__init__", real_init)
        return result

    return invoke


def test_cli_gaps(cli, tmp_path: Path):
    log = tmp_path / "gaps.jsonl"
    args = [
        "gaps",
        "--dims",
        "impact_area,method",
        "--year-from",
        "2000",
        "--log",
        str(log),
    ]
    result = cli([reply(json.dumps(judgement()))], *args)
    assert result.exit_code == 0, result.output
    assert "tagging artefact" in result.stdout and "Caveats" in result.stdout
    assert json.loads(log.read_text(encoding="utf-8"))["gaps_found"] == 2


def test_cli_gaps_json_no_llm(cli):
    result = cli(
        [], "gaps", "--dims", "impact_area,method", "--year-from", "2000",
        "--no-llm", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["judgement"] is None and len(data["cells"]) == 2


def test_cli_gaps_errors(cli, tmp_path: Path):
    assert cli([], "gaps", "--dims", "method").exit_code == 2
    # the toy catalogue has no sub_area tags: nothing to analyse, no gaps
    result = cli([], "gaps", "--no-llm")
    assert result.exit_code == 0 and "No gaps found." in result.stdout
