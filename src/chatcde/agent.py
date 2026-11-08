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
_CITE_GROUP_RE = re.compile(r"\[([^\[\]]*BORIS:[^\[\]]*)\]")
#: One reference inside such a group: the ID and an optional page.
_CITE_RE = re.compile(
    r"BORIS:\s*([\w.:/-]+?)"  # the record ID
    r"(?:\s*,?\s*pp?\.\s*(\d+)(?:\s*[-–]\s*\d+)?)?"  # optional page (range)
    r"\s*(?=[;,]|$)"
)

#: Message appended when the tool budget is used up.
BUDGET_NOTE = (
    "[The tool-call budget for this question is used up.] Answer now with the "
    "evidence you have, cite it as instructed, and say what is missing."
)

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


class AgentResult(BaseModel):
    """The answer to one question, with its evidence trail and provenance."""

    question: str
    answer: str
    citations: list[AnswerCitation]
    unknown_ids: list[str] = Field(
        description="Cited IDs that are not in the catalogue"
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
    for group in _CITE_GROUP_RE.finditer(answer):
        for m in _CITE_RE.finditer(group.group(1)):
            found.append(
                (m.group(1).rstrip(".,;"), int(m.group(2)) if m.group(2) else None)
            )
    return found


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
        budget = self.settings.max_tool_calls
        records: list[ToolCallRecord] = []
        usage: dict[str, int] = {}
        stop: StopReason = "answered"
        answer = ""
        rounds = 0
        while True:
            rounds += 1
            offer_tools = len(records) < budget
            message, finish, used = self._complete(offer_tools)
            for k, v in used.items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
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
        self.messages.append({"role": "assistant", "content": answer})
        cites, unknown = extract_citations(self.tool_ctx, answer, self._seen)
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
