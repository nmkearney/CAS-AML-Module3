"""``chatcde evaluate questions``: run test questions through the agent and score them.

Issue #28; see docs/decisions/0025-evaluate-questions.md. The questions live in
``tests/questions.yaml`` (written by Track A, #19), each with answers that can be
checked by code: records that must be cited, tools that must be called, words
and numbers that must appear. Scoring is deterministic: no LLM judges another
LLM, so a score changes only when the answer changes.

Every question is also held to three checks applied to all answers:

* ``answered``: the agent finished (no used-up tool budget, no cut-off);
* ``no_unknown_ids``: every cited ID is in the catalogue;
* ``cited_from_tools``: every cited record came back from a tool.

``run_questions`` asks each question once per model and run, in a fresh
conversation, and appends one JSON line per answer (score and full
:class:`~chatcde.agent.AgentResult`) to a results file. A rerun with the same file
skips what is done, so an interrupted evaluation resumes. ``rescore`` scores a
saved file again after the expectations change, with no model calls.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from chatcde.agent import CITE_GROUP_RE, Agent, AgentError, AgentResult
from chatcde.config import Config
from chatcde.store.endpoint import EndpointClient, EndpointError

log = logging.getLogger(__name__)

Kind = Literal["lookup", "search", "count", "gaps", "synthesis"]

#: Characters of the answer kept in each score, for the tables.
EXCERPT_CHARS = 300


class EvaluationError(Exception):
    """The questions file is missing or invalid, or a model is not configured."""


# --- the questions file ---------------------------------------------------------------


class Expectation(BaseModel):
    """What a good answer contains. Every field is optional."""

    model_config = ConfigDict(extra="forbid")

    records_any: list[str] = Field(
        default_factory=list, description="At least one of these must be cited"
    )
    records_all: list[str] = Field(
        default_factory=list, description="All of these must be cited"
    )
    tools: list[str] = Field(
        default_factory=list, description="Tools that must be called successfully"
    )
    mentions: list[str] = Field(
        default_factory=list, description="All must appear (case-insensitive)"
    )
    mentions_any: list[str] = Field(
        default_factory=list, description="At least one must appear"
    )
    numbers: list[float] = Field(
        default_factory=list, description="Numbers that must appear in the answer"
    )


class Question(BaseModel):
    """One test question with its known answer."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    kind: Kind
    expect: Expectation = Field(default_factory=Expectation)
    notes: str | None = None


class QuestionSet(BaseModel):
    """The whole ``tests/questions.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    questions: list[Question]

    @model_validator(mode="after")
    def _unique_ids(self) -> QuestionSet:
        seen: set[str] = set()
        dupes = {q.id for q in self.questions if q.id in seen or seen.add(q.id)}
        if dupes:
            raise ValueError(f"duplicate question ids: {', '.join(sorted(dupes))}")
        return self


def load_questions(path: Path) -> QuestionSet:
    """Read and validate a questions file.

    Raises:
        EvaluationError: The file is missing, not YAML, or not a valid set.
    """
    if not path.is_file():
        raise EvaluationError(
            f"No questions file at {path} (Track A writes it, issue #19)."
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return QuestionSet.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise EvaluationError(f"{path} is not a valid questions file:\n{exc}") from exc


# --- scoring --------------------------------------------------------------------------


class CheckResult(BaseModel):
    """One check of one answer."""

    name: str
    passed: bool
    detail: str = ""


class QuestionScore(BaseModel):
    """How one answer (question x model x run) scored."""

    question_id: str
    kind: Kind
    model: str
    run: int
    checks: list[CheckResult]
    passed: bool = Field(description="Every check passed")
    score: float = Field(description="Share of checks passed")
    record_recall: float | None = Field(
        description="Expected records cited / expected records (None: none expected)"
    )
    tool_calls: int
    seconds: float
    tokens: int
    stop_reason: str | None
    error: str | None = None
    answer_excerpt: str = ""


#: Thousands separators between digit groups: 1,234 / 1'234 / 1 234 / 1’234.
_THOUSANDS = re.compile(r"(?<=\d)[,'’   ](?=\d{3}(?!\d))")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> set[float]:
    """The numbers written in ``text``, ignoring citations and thousands separators."""
    plain = _THOUSANDS.sub("", CITE_GROUP_RE.sub(" ", text))
    return {float(n) for n in _NUMBER.findall(plain)}


def _fold(text: str) -> str:
    return text.casefold()


def score_answer(
    question: Question, result: AgentResult, run: int = 1
) -> QuestionScore:
    """Score one answer against its question's expectations (pure code)."""
    e = question.expect
    cited = [c.citation.record_id for c in result.citations]
    cited_set = set(cited)
    answer = _fold(result.answer)
    called = {t.name for t in result.tool_calls if t.ok}
    checks = [
        CheckResult(
            name="answered",
            passed=result.stop_reason == "answered",
            detail=f"stop reason: {result.stop_reason}",
        ),
        CheckResult(
            name="no_unknown_ids",
            passed=not result.unknown_ids,
            detail=", ".join(result.unknown_ids),
        ),
        CheckResult(
            name="cited_from_tools",
            passed=all(c.from_tools for c in result.citations),
            detail=", ".join(
                c.citation.record_id for c in result.citations if not c.from_tools
            ),
        ),
    ]
    if e.records_any:
        hit = [r for r in e.records_any if r in cited_set]
        checks.append(
            CheckResult(
                name="records_any",
                passed=bool(hit),
                detail=f"cited {len(hit)} of {len(e.records_any)}",
            )
        )
    if e.records_all:
        missing = [r for r in e.records_all if r not in cited_set]
        checks.append(
            CheckResult(
                name="records_all",
                passed=not missing,
                detail=f"missing: {', '.join(missing)}" if missing else "",
            )
        )
    if e.tools:
        missing = [t for t in e.tools if t not in called]
        checks.append(
            CheckResult(
                name="tools",
                passed=not missing,
                detail=f"not called: {', '.join(missing)}" if missing else "",
            )
        )
    if e.mentions:
        missing = [m for m in e.mentions if _fold(m) not in answer]
        checks.append(
            CheckResult(
                name="mentions",
                passed=not missing,
                detail=f"missing: {', '.join(missing)}" if missing else "",
            )
        )
    if e.mentions_any:
        hit = [m for m in e.mentions_any if _fold(m) in answer]
        checks.append(
            CheckResult(
                name="mentions_any",
                passed=bool(hit),
                detail=f"found: {', '.join(hit)}" if hit else "none found",
            )
        )
    if e.numbers:
        found = numbers_in(result.answer)
        missing_n = [n for n in e.numbers if float(n) not in found]
        checks.append(
            CheckResult(
                name="numbers",
                passed=not missing_n,
                detail=f"missing: {', '.join(f'{n:g}' for n in missing_n)}"
                if missing_n
                else "",
            )
        )
    expected = set(e.records_any) | set(e.records_all)
    recall = len(expected & cited_set) / len(expected) if expected else None
    passed = sum(c.passed for c in checks)
    return QuestionScore(
        question_id=question.id,
        kind=question.kind,
        model=result.model,
        run=run,
        checks=checks,
        passed=passed == len(checks),
        score=round(passed / len(checks), 3),
        record_recall=None if recall is None else round(recall, 3),
        tool_calls=len(result.tool_calls),
        seconds=result.seconds,
        tokens=int(result.usage.get("total_tokens", 0)),
        stop_reason=result.stop_reason,
        answer_excerpt=" ".join(result.answer.split())[:EXCERPT_CHARS],
    )


def failed_score(question: Question, model: str, run: int, error: str) -> QuestionScore:
    """The score of a question the agent could not answer (endpoint error)."""
    return QuestionScore(
        question_id=question.id,
        kind=question.kind,
        model=model,
        run=run,
        checks=[CheckResult(name="answered", passed=False, detail=error)],
        passed=False,
        score=0.0,
        record_recall=0.0
        if question.expect.records_any or question.expect.records_all
        else None,
        tool_calls=0,
        seconds=0.0,
        tokens=0,
        stop_reason=None,
        error=error,
    )


# --- running --------------------------------------------------------------------------


class ResultLine(BaseModel):
    """One line of a results file."""

    score: QuestionScore
    result: AgentResult | None = None


def read_results(path: Path) -> list[ResultLine]:
    """Read a results file (empty if it does not exist)."""
    if not path.is_file():
        return []
    lines = []
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip():
            continue
        try:
            lines.append(ResultLine.model_validate_json(text))
        except ValidationError as exc:
            raise EvaluationError(f"{path}, line {n}: not a result line") from exc
    return lines


def resolve_models(cfg: Config, which: str) -> list[str]:
    """``main`` / ``alternative`` / ``both`` -> agent model names.

    Raises:
        EvaluationError: ``alternative`` or ``both`` without an alternative model.
    """
    main, alt = cfg.models.agent, cfg.model_alternatives.agent
    if which == "main":
        return [main]
    if not alt:
        raise EvaluationError("No alternative agent model is set in the config.")
    return [alt] if which == "alternative" else [main, alt]


def run_questions(
    cfg: Config,
    questions: Iterable[Question],
    models: list[str],
    out_path: Path,
    *,
    runs: int = 1,
    client: EndpointClient | None = None,
    on_result: Callable[[QuestionScore], None] | None = None,
) -> list[QuestionScore]:
    """Ask every question with every model ``runs`` times; append to ``out_path``.

    Each question starts a fresh conversation. Answers already in ``out_path``
    (same question, model and run) are skipped, so a rerun resumes. An endpoint
    or agent error is recorded as a failed answer and the run goes on.

    Returns:
        The scores of every answer in ``out_path`` for these questions and
        models, old and new, in question order.
    """
    qs = list(questions)
    existing = read_results(out_path)
    done = {(x.score.question_id, x.score.model, x.score.run) for x in existing}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for model in models:
        todo = [
            (q, r)
            for q in qs
            for r in range(1, runs + 1)
            if (q.id, model, r) not in done
        ]
        if not todo:
            continue
        with (
            Agent(cfg, client, model=model) as agent,
            out_path.open("a", encoding="utf-8") as f,
        ):
            for q, r in todo:
                agent.reset()
                result: AgentResult | None = None
                try:
                    result = agent.ask(q.question)
                    score = score_answer(q, result, r)
                except (EndpointError, AgentError) as exc:
                    log.warning("question %s with %s failed: %s", q.id, model, exc)
                    score = failed_score(q, model, r, str(exc))
                f.write(ResultLine(score=score, result=result).model_dump_json() + "\n")
                f.flush()
                if on_result:
                    on_result(score)
    wanted = {q.id for q in qs}
    order = {q.id: i for i, q in enumerate(qs)}
    scores = [
        x.score
        for x in read_results(out_path)
        if x.score.question_id in wanted and x.score.model in models
    ]
    return sorted(
        scores, key=lambda s: (order[s.question_id], models.index(s.model), s.run)
    )


def rescore(path: Path, questions: Iterable[Question]) -> list[QuestionScore]:
    """Score the answers saved in ``path`` again, against the current expectations.

    Lines for questions no longer in the set are skipped; failed answers stay
    failed.
    """
    by_id = {q.id: q for q in questions}
    out = []
    for line in read_results(path):
        q = by_id.get(line.score.question_id)
        if q is None:
            continue
        if line.result is None:
            out.append(
                failed_score(
                    q, line.score.model, line.score.run, line.score.error or ""
                )
            )
        else:
            out.append(score_answer(q, line.result, line.score.run))
    return out


# --- summary --------------------------------------------------------------------------


class ModelSummary(BaseModel):
    """One model's results over all its answers."""

    model: str
    answers: int
    passed: int
    pass_rate: float
    mean_score: float
    mean_record_recall: float | None
    mean_tool_calls: float
    mean_seconds: float
    total_tokens: int
    errors: int
    by_kind: dict[str, dict[str, Any]] = Field(
        description="kind -> {answers, passed, pass_rate}"
    )
    failed: list[str] = Field(description="question_id/run of failed answers")


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def summarise(scores: list[QuestionScore]) -> list[ModelSummary]:
    """Per-model totals, in the order the models first appear."""
    models: list[str] = []
    for s in scores:
        if s.model not in models:
            models.append(s.model)
    out = []
    for m in models:
        ss = [s for s in scores if s.model == m]
        recalls = [s.record_recall for s in ss if s.record_recall is not None]
        kinds: dict[str, dict[str, Any]] = {}
        for s in ss:
            k = kinds.setdefault(s.kind, {"answers": 0, "passed": 0})
            k["answers"] += 1
            k["passed"] += int(s.passed)
        for k in kinds.values():
            k["pass_rate"] = round(k["passed"] / k["answers"], 3)
        passed = sum(s.passed for s in ss)
        out.append(
            ModelSummary(
                model=m,
                answers=len(ss),
                passed=passed,
                pass_rate=round(passed / len(ss), 3),
                mean_score=_mean([s.score for s in ss]),
                mean_record_recall=_mean(recalls) if recalls else None,
                mean_tool_calls=_mean([float(s.tool_calls) for s in ss]),
                mean_seconds=_mean([s.seconds for s in ss]),
                total_tokens=sum(s.tokens for s in ss),
                errors=sum(s.error is not None for s in ss),
                by_kind=dict(sorted(kinds.items())),
                failed=[f"{s.question_id}/{s.run}" for s in ss if not s.passed],
            )
        )
    return out


def select(
    qs: QuestionSet, ids: list[str] | None = None, kind: str | None = None
) -> list[Question]:
    """The questions to run: all, or those with the given IDs and/or kind.

    Raises:
        EvaluationError: An ID is not in the set, or nothing is left.
    """
    out = qs.questions
    if ids:
        known = {q.id for q in out}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise EvaluationError(f"unknown question ids: {', '.join(unknown)}")
        out = [q for q in out if q.id in ids]
    if kind:
        out = [q for q in out if q.kind == kind]
    if not out:
        raise EvaluationError("no questions match the selection")
    return out


def summary_json(scores: list[QuestionScore], min_pass_rate: float) -> str:
    """Scores and per-model summaries as one JSON document (for ``--json``)."""
    return json.dumps(
        {
            "min_pass_rate": min_pass_rate,
            "models": [m.model_dump() for m in summarise(scores)],
            "scores": [s.model_dump() for s in scores],
        },
        indent=2,
        ensure_ascii=False,
    )
