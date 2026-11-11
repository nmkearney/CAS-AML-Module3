"""Tests for ``chatcde ask`` and ``chatcde chat`` and their rendering (issue #26).

The model is the scripted fake from ``test_agent.py``; the tools run on the
fixture catalogue. No network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from chatcde.agent import AgentResult
from chatcde.cli import app
from chatcde.config import load_config
from chatcde.schemas import BundleManifest, Citation, Edition
from chatcde.workflows.answer import data_note, number_citations, render_result
from test_agent import MRD, ScriptedLLM, call, reply

SYN = "fixture-synthetic-04"


def write_config(tmp_path: Path, db: Path, data_dir: Path | None = None) -> Path:
    path = tmp_path / "o.yaml"
    lines = [
        "data:",
        f'  catalogue: "{db.as_posix()}"',
        f'  dir: "{(data_dir or tmp_path / "data").as_posix()}"',
        "logging:",
        "  file: null",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def run(fixture_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``run(replies, *args, input=None)`` invokes chatcde with a scripted model."""
    from chatcde.store import endpoint

    override = write_config(tmp_path, fixture_db)
    state: dict[str, ScriptedLLM] = {}

    def invoke(replies: list[dict], *args: str, input: str | None = None):
        llm = state["llm"] = ScriptedLLM(replies)
        real_init = endpoint.EndpointClient.__init__

        def init_with_fake(self, *a, **kw):  # type: ignore[no-untyped-def]
            kw["transport"] = httpx.MockTransport(llm)
            kw["sleep"] = lambda _: None
            real_init(self, *a, **kw)

        monkeypatch.setattr(endpoint.EndpointClient, "__init__", init_with_fake)
        runner = CliRunner(env={"COLUMNS": "200"})
        result = runner.invoke(app, ["--config", str(override), *args], input=input)
        monkeypatch.setattr(endpoint.EndpointClient, "__init__", real_init)
        return result

    invoke.state = state  # type: ignore[attr-defined]
    return invoke


TOOL_THEN_ANSWER = [
    reply(calls=[call("get_publication", {"record_id": MRD})]),
    reply(
        f"The editorial weaves together knowledges [BORIS:{MRD}, p. 2]. A living "
        f"lab is described elsewhere [BORIS:{SYN}; BORIS:{MRD}]."
    ),
]


# --- rendering ------------------------------------------------------------------------


def _result(answer: str, citations: list[tuple[str, bool]]) -> AgentResult:
    from chatcde.agent import AnswerCitation

    return AgentResult(
        question="q",
        answer=answer,
        citations=[
            AnswerCitation(
                citation=Citation(
                    record_id=rid,
                    title=f"Title [{rid}]",
                    year=2020,
                    doi="10.1/x" if rid == "a" else None,
                    boris_url=f"https://boris.test/{rid}",
                ),
                pages=[3] if rid == "a" else [],
                from_tools=seen,
            )
            for rid, seen in citations
        ],
        unknown_ids=["zzz"] if "zzz" in answer else [],
        tool_calls=[],
        stop_reason="answered",
        rounds=1,
        usage={"total_tokens": 1234},
        seconds=1.5,
        model="m",
        prompt_version="agent_system.v1",
        vocab_version="0.2",
        code_version="test",
        created_at=datetime(2026, 11, 8, tzinfo=UTC),
    )


def test_number_citations():
    r = _result(
        "One [BORIS:a, p. 3]. Two [BORIS:b; BORIS:a]. Bad [BORIS:zzz]. Plain [1].",
        [("a", True), ("b", True)],
    )
    assert number_citations(r) == "One [1, p. 3]. Two [2; 1]. Bad [?]. Plain [1]."


def test_render_result_lists_sources_and_warnings():
    r = _result(
        "Claim [BORIS:a, p. 3] and [BORIS:b] and [BORIS:zzz].",
        [("a", True), ("b", False)],
    )
    r = r.model_copy(update={"stop_reason": "budget"})
    console = Console(record=True, width=200)
    render_result(console, r)
    out = console.export_text()
    assert "Claim [1, p. 3] and [2] and [?]." in out
    assert (
        "[1] Title [a] (2020). pp. 3. https://doi.org/10.1/x | BORIS: https://boris.test/a"
        in out
    )
    assert (
        "[2] Title [b] (2020). BORIS: https://boris.test/b (not returned by any tool)"
        in out
    )
    assert "not catalogue records" in out and "zzz" in out
    assert "budget" in out
    assert "m | agent_system.v1 | 0 tool calls | 1.5 s | 1,234 tokens" in out


def test_data_note(fixture_db: Path, tmp_path: Path):
    cfg = load_config(write_config(tmp_path, fixture_db))
    note = data_note(cfg)
    assert note is not None and note.edition is Edition.FULL
    assert note.records == 34
    assert note.with_fulltext + note.abstract_only + note.metadata_only == 34
    assert note.with_fulltext > 0 and "local build" in note.text()

    data_dir = tmp_path / "bundle"
    data_dir.mkdir()
    manifest = BundleManifest(
        edition=Edition.OPEN,
        version="2026.11.0",
        built_at=datetime(2026, 11, 8, tzinfo=UTC),
        code_version="x",
        vocab_version="0.2",
        models={},
        record_count=34,
        fulltext_count=10,
        files={},
    )
    (data_dir / "manifest.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / "o2").mkdir()
    cfg = load_config(write_config(tmp_path / "o2", fixture_db, data_dir))
    text = data_note(cfg).text()  # type: ignore[union-attr]
    assert "open edition (bundle 2026.11.0)" in text and "Restricted" in text

    cfg = load_config(write_config(tmp_path / "o2", tmp_path / "none.duckdb"))
    assert data_note(cfg) is None


def test_render_result_lists_count_queries_and_a_rewrite():
    from chatcde.agent import CountQuery

    query = CountQuery(
        tool="count_by",
        arguments={"row": "method", "filters": {"impact_areas": ["SG"], "sdgs": []}},
        summary="1,275 records match, 3 of them have no method tag",
    )
    r = _result("Few [BORIS:a].", [("a", True)]).model_copy(
        update={"counts": [query], "retried_ids": ["count_by"]}
    )
    console = Console(record=True, width=200)
    render_result(console, r)
    out = console.export_text()
    assert "Counts (the queries behind the numbers)" in out
    assert (
        "- count_by(row=method, impact_areas=[SG]): 1,275 records match, "
        "3 of them have no method tag" in out
    )
    assert "it was rewritten" in out and "count_by" in out


# --- chatcde ask ----------------------------------------------------------------------


def test_ask_prints_answer_sources_and_data_note(run):
    result = run(TOOL_THEN_ANSWER, "ask", "What is the MRD editorial about?")
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "weaves together knowledges [1, p. 2]" in out
    assert "elsewhere [2; 1]" in out
    assert "Sources" in out and "https://doi.org/10.1659/mrd.4204" in out
    assert "(not returned by any tool)" in out  # SYN was cited but never looked up
    assert "Data: full edition (local build); 34 publications" in out
    assert "-> get_publication(record_id=" in result.stderr  # tool progress


def test_ask_json_and_log(run, tmp_path: Path):
    log = tmp_path / "logs" / "answers.jsonl"
    result = run(
        TOOL_THEN_ANSWER, "ask", "q", "--json", "--hide-tools", "--log", str(log)
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["question"] == "q" and data["stop_reason"] == "answered"
    assert [c["citation"]["record_id"] for c in data["citations"]] == [MRD, SYN]
    assert "->" not in result.stderr
    run(TOOL_THEN_ANSWER, "ask", "again", "--json", "--log", str(log))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["question"] for x in lines] == ["q", "again"]


def test_ask_alternative_model(run):
    result = run([reply("Hello.")], "ask", "q", "--alternative", "--json")
    assert result.exit_code == 0, result.output
    cfg = load_config()
    assert run.state["llm"].requests[0]["model"] == cfg.model_alternatives.agent


def test_ask_endpoint_failure(run):
    result = run([], "ask", "q")  # the script is empty: HTTP 500 after retries
    assert result.exit_code == 1
    assert "The agent failed" in result.stderr


def test_ask_without_catalogue(tmp_path: Path):
    override = write_config(tmp_path, tmp_path / "none.duckdb")
    result = CliRunner().invoke(app, ["--config", str(override), "ask", "q"])
    assert result.exit_code == 1 and "No catalogue" in result.output


# --- chatcde chat ---------------------------------------------------------------------


def test_chat_keeps_history_and_resets(run):
    replies = [reply("First answer."), reply("Second answer."), reply("Third.")]
    result = run(
        replies, "chat", "--hide-tools", input="one\n\ntwo\n/reset\nthree\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "First answer." in out and "Second answer." in out and "Third." in out
    assert "New conversation." in out and "Data: full edition" in out
    requests = run.state["llm"].requests
    assert [m["role"] for m in requests[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert [m["role"] for m in requests[2]["messages"]] == ["system", "user"]


def test_chat_ends_on_eof_and_survives_errors(run):
    result = run([], "chat", input="a question\n")  # endpoint fails, then EOF
    assert result.exit_code == 0, result.output
    assert "The agent failed" in result.stderr
