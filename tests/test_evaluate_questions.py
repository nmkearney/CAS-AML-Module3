"""Tests for ``chatcde evaluate questions`` (issue #28). No network.

The agent is the scripted fake from ``test_agent.py``; its tools run on the
fixture catalogue.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from chatcde.agent import AgentResult, AnswerCitation, ToolCallRecord
from chatcde.cli import app
from chatcde.config import load_config
from chatcde.schemas import Citation
from chatcde.store.endpoint import EndpointClient
from chatcde.workflows.evaluate import (
    EvaluationError,
    Question,
    load_questions,
    numbers_in,
    read_results,
    rescore,
    resolve_models,
    run_questions,
    score_answer,
    select,
    summarise,
)
from test_agent import MRD, ScriptedLLM, call, reply

QUESTIONS = f"""\
version: 1
questions:
  - id: q1
    question: "What is the MRD editorial about?"
    kind: lookup
    expect:
      records_any: ["{MRD}"]
      tools: [get_publication]
      mentions: ["knowledges"]
  - id: q2
    question: "How many records are there?"
    kind: count
    expect:
      numbers: [1234]
"""

TOOL_THEN_ANSWER = [
    reply(calls=[call("get_publication", {"record_id": MRD})]),
    reply(f"The editorial weaves together knowledges [BORIS:{MRD}, p. 2]."),
]


def write(tmp_path: Path, text: str = QUESTIONS, name: str = "q.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def config_file(tmp_path: Path, db: Path, extra: str = "") -> Path:
    path = tmp_path / "o.yaml"
    path.write_text(
        f'data:\n  catalogue: "{db.as_posix()}"\nlogging:\n  file: null\n'
        f'evaluation:\n  results_dir: "{(tmp_path / "eval").as_posix()}"\n' + extra,
        encoding="utf-8",
    )
    return path


def client(llm: ScriptedLLM) -> EndpointClient:
    return EndpointClient(
        "https://llm.test/v1", "k", timeout_s=5, max_retries=0,
        transport=httpx.MockTransport(llm), sleep=lambda _: None,
    )  # fmt: skip


def result(answer: str, cited: list[tuple[str, bool]] = (), **kw) -> AgentResult:  # type: ignore[assignment]
    data = {
        "question": "q",
        "answer": answer,
        "citations": [
            AnswerCitation(
                citation=Citation(
                    record_id=r, title="t", year=2020, doi=None, boris_url="u"
                ),
                from_tools=seen,
            )
            for r, seen in cited
        ],
        "unknown_ids": [],
        "tool_calls": [
            ToolCallRecord(
                name="get_publication",
                arguments={},
                ok=True,
                seconds=0.1,
                result_chars=10,
            )
        ],  # fmt: skip
        "stop_reason": "answered",
        "rounds": 2,
        "usage": {"total_tokens": 100},
        "seconds": 1.0,
        "model": "m",
        "prompt_version": "p",
        "vocab_version": "v",
        "code_version": "c",
        "created_at": datetime(2026, 11, 8, tzinfo=UTC),
    }
    data.update(kw)
    return AgentResult(**data)


# --- questions file -------------------------------------------------------------------


def test_load_and_select(tmp_path: Path):
    qs = load_questions(write(tmp_path))
    assert [q.id for q in qs.questions] == ["q1", "q2"]
    assert [q.id for q in select(qs, kind="count")] == ["q2"]
    assert [q.id for q in select(qs, ids=["q1"])] == ["q1"]
    with pytest.raises(EvaluationError, match="unknown question ids"):
        select(qs, ids=["nope"])
    with pytest.raises(EvaluationError, match="no questions"):
        select(qs, kind="gaps")


@pytest.mark.parametrize(
    "text, match",
    [
        (QUESTIONS.replace("mentions:", "mention:"), "not a valid"),  # typo
        (QUESTIONS.replace("id: q2", "id: q1"), "duplicate question ids"),
        ("version: 2\nquestions: []\n", "not a valid"),
        ("questions: [", "not a valid"),
    ],
)
def test_invalid_files(tmp_path: Path, text: str, match: str):
    with pytest.raises(EvaluationError, match=match):
        load_questions(write(tmp_path, text))


def test_missing_file(tmp_path: Path):
    with pytest.raises(EvaluationError, match="#19"):
        load_questions(tmp_path / "none.yaml")


# --- scoring --------------------------------------------------------------------------


def test_numbers_in():
    text = "We found 1,234 records, 1'500 and 2 000 more, 12.5% [BORIS:abc-99, p. 7]."
    assert numbers_in(text) == {1234.0, 1500.0, 2000.0, 12.5}


def test_score_passes_all_checks():
    q = Question(
        id="q", question="?", kind="lookup",
        expect={"records_any": ["a", "b"], "records_all": ["a"],
                "tools": ["get_publication"], "mentions": ["Laos"],
                "mentions_any": ["land", "water"], "numbers": [42]},
    )  # fmt: skip
    s = score_answer(q, result("In LAOS, 42 land deals [BORIS:a].", [("a", True)]))
    assert s.passed and s.score == 1.0 and len(s.checks) == 9
    assert s.record_recall == 0.5  # a of {a, b}
    assert s.tool_calls == 1 and s.tokens == 100


def test_score_failures_are_named():
    q = Question(
        id="q", question="?", kind="search",
        expect={"records_all": ["a", "b"], "tools": ["find_gaps"],
                "mentions": ["Laos"], "numbers": [7]},
    )  # fmt: skip
    r = result(
        "Nothing [BORIS:a].",
        [("a", False)],
        unknown_ids=["zzz"],
        stop_reason="budget",
    )
    s = score_answer(q, r)
    failed = {c.name: c.detail for c in s.checks if not c.passed}
    assert set(failed) == {
        "answered", "no_unknown_ids", "cited_from_tools",
        "records_all", "tools", "mentions", "numbers",
    }  # fmt: skip
    assert failed["records_all"] == "missing: b" and failed["numbers"] == "missing: 7"
    assert not s.passed and s.score == 0.0


def test_summarise():
    q = Question(id="q", question="?", kind="count", expect={"numbers": [1]})
    scores = [
        score_answer(q, result("1")),
        score_answer(q, result("2"), run=2),
        score_answer(q, result("1", model="other")),
    ]
    a, b = summarise(scores)
    assert (a.model, a.answers, a.passed, a.pass_rate) == ("m", 2, 1, 0.5)
    assert a.failed == ["q/2"] and a.by_kind["count"]["pass_rate"] == 0.5
    assert a.mean_record_recall is None
    assert (b.model, b.pass_rate) == ("other", 1.0)


# --- running --------------------------------------------------------------------------


def test_run_both_models_resume_and_rescore(fixture_db: Path, tmp_path: Path):
    cfg = load_config(config_file(tmp_path, fixture_db))
    q1 = select(load_questions(write(tmp_path)), ids=["q1"])
    models = resolve_models(cfg, "both")
    assert models == [cfg.models.agent, cfg.model_alternatives.agent]
    llm = ScriptedLLM(TOOL_THEN_ANSWER * 2)
    out = tmp_path / "eval" / "r.jsonl"
    seen = []
    scores = run_questions(
        cfg, q1, models, out, client=client(llm), on_result=seen.append
    )
    assert [s.model for s in scores] == models and all(s.passed for s in scores)
    assert [r["model"] for r in llm.requests] == [models[0]] * 2 + [models[1]] * 2
    assert len(seen) == 2 and len(read_results(out)) == 2

    # A rerun with the same file asks nothing new.
    again = run_questions(cfg, q1, models, out, client=client(ScriptedLLM([])))
    assert len(again) == 2

    # Stricter expectations, scored from the saved answers.
    strict = load_questions(
        write(tmp_path, QUESTIONS.replace('["knowledges"]', '["Bhutan"]'), "s.yaml")
    )
    re = rescore(out, strict.questions)
    assert len(re) == 2 and not any(s.passed for s in re)


def test_endpoint_error_is_recorded(fixture_db: Path, tmp_path: Path):
    cfg = load_config(config_file(tmp_path, fixture_db))
    qs = load_questions(write(tmp_path)).questions
    out = tmp_path / "r.jsonl"
    scores = run_questions(cfg, qs, ["m"], out, client=client(ScriptedLLM([])))
    assert len(scores) == 2 and all(s.error and not s.passed for s in scores)
    assert scores[0].record_recall == 0.0 and scores[1].record_recall is None
    assert [x.result for x in read_results(out)] == [None, None]


def test_resolve_models_without_alternative(fixture_db: Path, tmp_path: Path):
    extra = "model_alternatives:\n  agent: null\n"
    cfg = load_config(config_file(tmp_path, fixture_db, extra))
    assert resolve_models(cfg, "main") == [cfg.models.agent]
    with pytest.raises(EvaluationError):
        resolve_models(cfg, "both")


# --- CLI ------------------------------------------------------------------------------


@pytest.fixture
def cli(fixture_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``cli(replies, *args)`` runs chatcde with the scripted model."""
    from chatcde.store import endpoint

    override = config_file(tmp_path, fixture_db)

    def invoke(replies: list[dict], *args: str):  # type: ignore[no-untyped-def]
        llm = ScriptedLLM(replies)
        real_init = endpoint.EndpointClient.__init__

        def init_with_fake(self, *a, **kw):  # type: ignore[no-untyped-def]
            kw["transport"] = httpx.MockTransport(llm)
            kw["sleep"] = lambda _: None
            kw["max_retries"] = 0
            real_init(self, *a, **kw)

        monkeypatch.setattr(endpoint.EndpointClient, "__init__", init_with_fake)
        runner = CliRunner(env={"COLUMNS": "200"})
        res = runner.invoke(
            app, ["--config", str(override), "evaluate", "questions", *args]
        )
        monkeypatch.setattr(endpoint.EndpointClient, "__init__", real_init)
        return res

    return invoke


def test_cli_passes(cli, tmp_path: Path):
    qfile = write(tmp_path)
    res = cli(TOOL_THEN_ANSWER, "--file", str(qfile), "--id", "q1", "--models", "main")
    assert res.exit_code == 0, res.output
    assert "Answers" in res.stdout and "1 of 1" in res.stdout
    assert "q1 (" in res.stderr and "pass" in res.stderr
    assert len(list((tmp_path / "eval").glob("questions-*.jsonl"))) == 1


def test_cli_below_threshold_json_and_rescore(cli, tmp_path: Path):
    qfile = write(tmp_path)
    out = tmp_path / "r.jsonl"
    res = cli(
        [reply("No idea."), reply("About 1,234.")],
        "--file", str(qfile), "--models", "main", "--out", str(out), "--json",
    )  # fmt: skip
    assert res.exit_code == 1  # q1 fails: 50% < 80%
    data = json.loads(res.stdout)
    assert data["models"][0]["pass_rate"] == 0.5
    assert [s["passed"] for s in data["scores"]] == [False, True]

    res = cli([], "--file", str(qfile), "--rescore", str(out), "--id", "q2")
    assert res.exit_code == 0, res.output
    assert "1 of 1" in res.stdout


def test_cli_bad_file(cli, tmp_path: Path):
    res = cli([], "--file", str(tmp_path / "none.yaml"))
    assert res.exit_code == 2 and "#19" in res.stderr
    res = cli([], "--file", str(write(tmp_path, "version: 1\nquestions: [{id: x}]\n")))
    assert res.exit_code == 2


def test_repo_questions_file_is_valid():
    """``tests/questions.yaml`` (#19) loads with the format of decision 0025."""
    path = Path(__file__).parent / "questions.yaml"
    qs = load_questions(path)
    assert qs.questions and all(q.notes for q in qs.questions)
    gap = next(q for q in qs.questions if q.id == "gaps-sg-methods")
    assert gap.kind == "gaps" and gap.expect.tools == ["find_gaps"]  # #67
