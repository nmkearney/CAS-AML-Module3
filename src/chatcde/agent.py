"""The agent: a local LLM that answers questions by calling ChatCDE's tools.

Issue #25; see docs/decisions/0022-agent-loop.md. One question runs like this:

1. The conversation (system prompt, earlier turns, the question) and the tool
   definitions (:mod:`chatcde.tools.specs`, generated from the tool functions)
   go to ``/chat/completions`` of the local endpoint, with the ``agent`` role
   model (``models.agent``).
2. While the model asks for tools, each call is validated and run
   (:func:`chatcde.tools.specs.run_tool`) and the result goes back as a ``tool``
   message. Errors go back too, so the model can correct itself.
3. At most ``agent.max_tool_calls`` calls per question. When they are used up,
   the model is told so and asked once more, without tools, to answer.
4. The answer cites records as ``[BORIS:<record_id>]`` or
   ``[BORIS:<record_id>, p. <n>]``. :func:`extract_citations` finds them, looks
   them up in the catalogue and flags any ID the tools never returned.
5. References to IDs that are not records (the model has cited tool names such
   as ``[BORIS:count_by]``) are sent back once with :data:`CITATION_NOTE`
   (``agent.citation_retry``); any left are removed from the answer and kept in
   :attr:`AgentResult.unknown_ids` (decision 0035).
6. Counts are not cited as records: every successful call of a counting tool
   (:data:`COUNT_TOOLS`) is kept as a :class:`CountQuery`, with its filters and
   denominator, and listed under the answer.

The :class:`AgentResult` records the model, prompt version, vocabulary version
and code version, plus every tool call, so answers can be reproduced and
evaluated (``chatcde evaluate questions``, #28). Rendering for the terminal is
the job of ``chatcde ask`` / ``chat`` (#26).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from chatcde.config import Config
from chatcde.harvest.common import code_version, now
from chatcde.schemas import Citation
from chatcde.store.endpoint import EndpointClient
from chatcde.tools.context import ToolContext, use_context
from chatcde.tools.publications import citation, records_by_id
from chatcde.tools.specs import ToolSpec, run_tool, tool_specs

log = logging.getLogger(__name__)

#: One ``[...]`` group that contains at least one ``BORIS:`` reference.
CITE_GROUP_RE = re.compile(r"\[([^\[\]]*BORIS:[^\[\]]*)\]")
#: One reference inside such a group: the ID and an optional page.
CITE_RE = re.compile(
    r"BORIS:\s*([\w.:/-]+?)"  # the record ID
    r"(?:\s*,?\s*pp?\.\s*(\d+)(?:\s*[-–]\s*\d+)?)?"  # optional page (range)
    r"\s*(?=[;,]|$)"
)

#: Message appended when the tool budget is used up.
BUDGET_NOTE = (
    "[The tool-call budget for this question is used up.] Answer now with the "
    "evidence you have, cite it as instructed, and say what is missing."
)

#: Message sent once when the answer cites IDs that are not records.
CITATION_NOTE = (
    "[Your answer cites references that are not records: {ids}.] Rewrite the "
    "whole answer. Use [BORIS:<record_id>] only for record_ids that appeared in "
    "tool results. Do not cite tools or counts: give each number in the text with "
    "its denominator; the count queries are listed under the answer automatically."
)

#: Tools whose results are counts; their queries are listed under the answer.
COUNT_TOOLS = ("count_by", "find_gaps", "coverage_report", "trend")

StopReason = Literal["answered", "budget", "length"]


class AgentError(Exception):
    """The endpoint returned no usable reply."""


class ToolCallRecord(BaseModel):
    """One tool call made while answering, for the log and for evaluation."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    error: str | None = None
    seconds: float
    result_chars: int


class AnswerCitation(BaseModel):
    """A record cited in the answer."""

    citation: Citation
    pages: list[int] = Field(default_factory=list)
    from_tools: bool = Field(
        description="The record appeared in a tool result of this conversation"
    )


class CountQuery(BaseModel):
    """A count an answer may rest on: the tool call and its denominator."""

    tool: str
    arguments: dict[str, Any]
    summary: str = Field(description="Denominator and size of the result")

    def text(self) -> str:
        """One line, e.g. ``count_by(row=method, impact_areas=[SG]): 1,275 ...``."""
        return f"{self.tool}({format_arguments(self.arguments)}): {self.summary}"


def format_arguments(args: dict[str, Any]) -> str:
    """Tool arguments as short ``key=value`` text; filters are flattened.

    Empty values are left out, lists print without quotes (``[SG, TE]``).
    """

    def show(v: Any) -> str:
        if isinstance(v, list):
            return "[" + ", ".join(show(x) for x in v) + "]"
        return str(v)

    flat: dict[str, Any] = {}
    for key, value in args.items():
        if key == "filters" and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return ", ".join(
        f"{k}={show(v)}" for k, v in flat.items() if v not in (None, [], {}, "")
    )


def summarise_count(tool: str, data: Any) -> str:
    """What a counting tool's result says about its denominator, in a few words."""
    if isinstance(data, dict) and tool == "count_by":
        dims = data.get("row_dimension") or "?"
        if data.get("col_dimension"):
            dims += f" or {data['col_dimension']}"
        return (
            f"{data.get('total_records', 0):,} records match, "
            f"{data.get('untagged_records', 0):,} of them have no {dims} tag"
        )
    if isinstance(data, dict) and tool == "coverage_report":
        return (
            f"{data.get('total_records', 0):,} records: "
            f"{data.get('with_fulltext', 0):,} full text, "
            f"{data.get('abstract_only', 0):,} abstract only, "
            f"{data.get('metadata_only', 0):,} metadata only"
        )
    if isinstance(data, list) and tool == "find_gaps":
        return f"{len(data)} thin cells (count at most max_ratio x expected)"
    return "see the tool result"


class AgentResult(BaseModel):
    """The answer to one question, with its evidence trail and provenance."""

    question: str
    answer: str
    citations: list[AnswerCitation]
    unknown_ids: list[str] = Field(
        description="Cited IDs that are not in the catalogue (removed from answer)"
    )
    retried_ids: list[str] = Field(
        default_factory=list,
        description="Unknown IDs in the first answer that made the agent ask again",
    )
    counts: list[CountQuery] = Field(
        default_factory=list, description="Count queries run for this answer"
    )
    tool_calls: list[ToolCallRecord]
    stop_reason: StopReason
    rounds: int
    usage: dict[str, int]
    seconds: float
    model: str
    prompt_version: str
    vocab_version: str
    code_version: str
    created_at: datetime


def load_prompt(prompts_dir: Path, name: str) -> str:
    """Read the versioned prompt ``<prompts_dir>/<name>.md``."""
    return (prompts_dir / f"{name}.md").read_text(encoding="utf-8").strip()


def parse_citations(answer: str) -> list[tuple[str, int | None]]:
    """Find ``[BORIS:<id>]`` and ``[BORIS:<id>, p. <n>]`` references, in order.

    Several references may share one bracket (``[BORIS:a; BORIS:b, p. 3]``).
    """
    found = []
    for group in CITE_GROUP_RE.finditer(answer):
        for m in CITE_RE.finditer(group.group(1)):
            found.append(
                (m.group(1).rstrip(".,;"), int(m.group(2)) if m.group(2) else None)
            )
    return found


#: Typographic characters models put in references, mapped to plain ones.
#: gpt-oss writes U+2011 (non-breaking hyphen) and U+202F (narrow no-break
#: space) inside references; en dashes are kept (page ranges).
_REFERENCE_CHARS = str.maketrans(
    {
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
    }
)


def normalise_references(answer: str) -> str:
    """Replace typographic hyphens and spaces inside ``[BORIS:...]`` groups.

    Without this, ``[BORIS: ab12‑cd]`` is not recognised as a reference
    at all, so it is neither resolved nor checked.
    """
    return CITE_GROUP_RE.sub(lambda g: g.group(0).translate(_REFERENCE_CHARS), answer)


def _ref_id(m: re.Match[str]) -> str:
    """The record ID of one :data:`CITE_RE` match, without trailing punctuation."""
    return m.group(1).rstrip(".,;")


#: A citation group with the whitespace before it (removed with the group).
_GROUP_WITH_SPACE_RE = re.compile(r"(\s*)" + CITE_GROUP_RE.pattern)


def strip_references(answer: str, drop: set[str]) -> str:
    """Remove references to the IDs in ``drop``; drop groups left empty.

    ``"A [BORIS:x; BORIS:count_by]."`` with ``drop={"count_by"}`` becomes
    ``"A [BORIS:x]."``; ``"B [BORIS:count_by]."`` becomes ``"B."``.
    """

    def replace(group: re.Match[str]) -> str:
        refs = list(CITE_RE.finditer(group.group(2)))
        kept = [m.group(0).strip(" ,;") for m in refs if _ref_id(m) not in drop]
        if len(kept) == len(refs):
            return group.group(0)  # nothing to drop here: leave it as written
        return f"{group.group(1)}[{'; '.join(kept)}]" if kept else ""

    return _GROUP_WITH_SPACE_RE.sub(replace, answer) if drop else answer


def record_ids_in(data: Any) -> set[str]:
    """All record IDs in a tool result (``record_id`` values, ``record_ids`` lists)."""
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "record_id" and isinstance(v, str):
                    out.add(v)
                elif k == "record_ids" and isinstance(v, list):
                    out.update(x for x in v if isinstance(x, str))
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def extract_citations(
    ctx: ToolContext, answer: str, seen: set[str]
) -> tuple[list[AnswerCitation], list[str]]:
    """Resolve the answer's references against the catalogue.

    Returns:
        The cited records in order of first citation, each with the pages cited
        and whether a tool returned it, and the cited IDs not in the catalogue.
    """
    refs = parse_citations(answer)
    order: list[str] = []
    pages: dict[str, list[int]] = {}
    for rid, page in refs:
        if rid not in pages:
            order.append(rid)
            pages[rid] = []
        if page is not None and page not in pages[rid]:
            pages[rid].append(page)
    if not order:
        return [], []
    with ctx.catalogue() as con:
        records = records_by_id(con, order)
    cites = [
        AnswerCitation(
            citation=citation(records[rid], pages[rid][0] if pages[rid] else None),
            pages=sorted(pages[rid]),
            from_tools=rid in seen,
        )
        for rid in order
        if rid in records
    ]
    unknown = [rid for rid in order if rid not in records]
    return cites, unknown


class Agent:
    """A conversation with the agent model; each :meth:`ask` adds one turn.

    Args:
        cfg: Configuration (``models.agent``, ``agent`` and ``llm`` sections).
        client: Endpoint client; by default one is built from the config. It is
            also used by the tools (query embeddings, reranking).
        model: Override for ``models.agent`` (e.g. the alternative, for #28).
        specs: Override for the tools offered (tests); by default ``agent.tools``.
        on_tool_call: Called after each tool call (for progress output).
    """

    def __init__(
        self,
        cfg: Config,
        client: EndpointClient | None = None,
        *,
        model: str | None = None,
        specs: dict[str, ToolSpec] | None = None,
        on_tool_call: Callable[[ToolCallRecord], None] | None = None,
    ) -> None:
        """Load the system prompt and tool definitions; open no connections yet."""
        self.cfg = cfg
        self.settings = cfg.agent
        self.model = model or cfg.models.agent
        self._owns_client = client is None
        self.client = client or EndpointClient.from_config(cfg)
        self.tool_ctx = ToolContext(cfg, self.client)
        self.specs = specs if specs is not None else tool_specs(self.settings.tools)
        self.on_tool_call = on_tool_call
        self.prompt_version = self.settings.system_prompt
        system = load_prompt(cfg.prompts.dir, self.prompt_version).replace(
            "{max_tool_calls}", str(self.settings.max_tool_calls)
        )
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        self._seen: set[str] = set()
        self._counts: list[CountQuery] = []  # this turn's count queries

    def __enter__(self) -> Agent:
        """Use the agent in a ``with`` block."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the endpoint client at the end of a ``with`` block."""
        self.close()

    def close(self) -> None:
        """Close the endpoint client if the agent created it."""
        if self._owns_client:
            self.client.close()

    def reset(self) -> None:
        """Forget the conversation (keep the system prompt)."""
        del self.messages[1:]
        self._seen.clear()

    def tool_definitions(self) -> list[dict[str, Any]]:
        """The OpenAI ``tools`` list sent with each request."""
        return [s.definition() for s in self.specs.values()]

    def _complete(self, with_tools: bool) -> tuple[dict[str, Any], str | None, dict]:
        """One ``/chat/completions`` request; returns message, finish reason, usage.

        Raises:
            AgentError: If the reply has no message.
            chatcde.store.endpoint.EndpointError: If the request fails.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            **self.settings.extra_body,
        }
        if with_tools and self.specs:
            payload["tools"] = self.tool_definitions()
            payload["tool_choice"] = "auto"
        body = self.client.post_json("/chat/completions", payload)
        choices = body.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise AgentError(f"no message in the reply: {str(body)[:200]}")
        return (
            choices[0]["message"],
            choices[0].get("finish_reason"),
            body.get("usage") or {},
        )

    def _call_tool(self, call: dict[str, Any]) -> tuple[str, ToolCallRecord]:
        """Run one requested tool call; return the tool message and a log record."""
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        spec = self.specs.get(name)
        if spec is None:
            content = json.dumps(
                {"error": f"unknown tool {name!r}; use one of {', '.join(self.specs)}"}
            )
            return content, ToolCallRecord(
                name=name, arguments={}, ok=False, error="unknown tool",
                seconds=0.0, result_chars=len(content),
            )  # fmt: skip
        s = self.settings
        with use_context(self.tool_ctx):
            outcome = run_tool(
                spec,
                fn.get("arguments"),
                max_chars=s.max_tool_result_chars,
                omit_fields=s.omit_fields,
                max_ids=s.max_ids_per_list,
            )
        if outcome.ok:
            self._seen |= record_ids_in(outcome.data)
            if name in COUNT_TOOLS:
                query = CountQuery(
                    tool=name,
                    arguments=outcome.arguments,
                    summary=summarise_count(name, outcome.data),
                )
                if query not in self._counts:
                    self._counts.append(query)
        log.info(
            "tool %s(%s): %s in %.2f s",
            name,
            json.dumps(outcome.arguments, ensure_ascii=False)[:200],
            "ok" if outcome.ok else f"error: {outcome.error}",
            outcome.seconds,
        )
        return outcome.content, ToolCallRecord(
            name=name,
            arguments=outcome.arguments,
            ok=outcome.ok,
            error=outcome.error,
            seconds=outcome.seconds,
            result_chars=len(outcome.content),
        )

    def ask(self, question: str) -> AgentResult:
        """Answer one question, calling tools as the model asks; keep the turn.

        Raises:
            AgentError: If the endpoint returns no usable message.
            chatcde.store.endpoint.EndpointError: If the endpoint fails.
        """
        started = time.monotonic()
        self.messages.append({"role": "user", "content": question})
        self._counts = []
        budget = self.settings.max_tool_calls
        records: list[ToolCallRecord] = []
        usage: dict[str, int] = {}
        stop: StopReason = "answered"
        answer = ""
        rounds = 0

        def complete(with_tools: bool) -> tuple[dict[str, Any], str | None]:
            message, finish, used = self._complete(with_tools)
            for k, v in used.items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
            return message, finish

        while True:
            rounds += 1
            offer_tools = len(records) < budget
            message, finish = complete(offer_tools)
            calls = message.get("tool_calls") or []
            if calls and offer_tools:
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": calls,
                    }
                )
                for i, call in enumerate(calls):
                    if len(records) >= budget:
                        content = json.dumps(
                            {"error": "not run: the tool-call budget is used up"}
                        )
                        rec = ToolCallRecord(
                            name=(call.get("function") or {}).get("name") or "",
                            arguments={}, ok=False, error="budget",
                            seconds=0.0, result_chars=len(content),
                        )  # fmt: skip
                    else:
                        content, rec = self._call_tool(call)
                    records.append(rec)
                    if self.on_tool_call:
                        self.on_tool_call(rec)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or f"call_{rounds}_{i}",
                            "content": content,
                        }
                    )
                if len(records) >= budget:
                    self.messages.append({"role": "user", "content": BUDGET_NOTE})
                    stop = "budget"
                continue
            answer = (message.get("content") or "").strip()
            if finish == "length":
                stop = "length"
            break
        answer = normalise_references(answer)
        cites, unknown = extract_citations(self.tool_ctx, answer, self._seen)
        retried: list[str] = []
        if unknown and self.settings.citation_retry:
            retried = unknown
            log.info("answer cites unknown IDs %s; asking once more", unknown)
            note = CITATION_NOTE.format(ids=", ".join(unknown))
            self.messages += [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": note},
            ]
            rounds += 1
            message, finish = complete(False)
            del self.messages[-2:]  # keep only the corrected answer in the history
            fixed = normalise_references((message.get("content") or "").strip())
            if fixed:
                answer = fixed
                stop = "length" if finish == "length" else stop
                cites, unknown = extract_citations(self.tool_ctx, answer, self._seen)
        answer = strip_references(answer, set(unknown))
        self.messages.append({"role": "assistant", "content": answer})
        if unknown or any(not c.from_tools for c in cites):
            log.warning(
                "answer cites %d unknown IDs and %d records no tool returned",
                len(unknown),
                sum(not c.from_tools for c in cites),
            )
        return AgentResult(
            question=question,
            answer=answer,
            citations=cites,
            unknown_ids=unknown,
            retried_ids=retried,
            counts=list(self._counts),
            tool_calls=records,
            stop_reason=stop,
            rounds=rounds,
            usage=usage,
            seconds=round(time.monotonic() - started, 2),
            model=self.model,
            prompt_version=self.prompt_version,
            vocab_version=self.cfg.vocab.version,
            code_version=code_version(),
            created_at=now(),
        )


def ask(cfg: Config, question: str, **kwargs: Any) -> AgentResult:
    """Answer one question in a fresh conversation (convenience wrapper)."""
    with Agent(cfg, **kwargs) as agent:
        return agent.ask(question)
