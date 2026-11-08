"""Tests for tool definitions (tools/specs.py) and the agent loop (agent.py), #25.

No network: a scripted fake ``/chat/completions`` replays the model's replies,
and the tools run for real on the fixture catalogue (get_publication, count_by
and list_vocabulary need no search index).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from chatcde.agent import (
    BUDGET_NOTE,
    Agent,
    AgentError,
    parse_citations,
    record_ids_in,
)
from chatcde.config import Config, load_config
from chatcde.schemas import Dimension, Filters
from chatcde.store.endpoint import EndpointClient
from chatcde.tools.context import ToolError
from chatcde.tools.specs import (
    CORE_TOOLS,
    build_spec,
    compact,
    parse_docstring,
    render,
    run_tool,
    tool_specs,
)

MRD = "2760e535-c973-4524-ab84-3a1c71b9bf3a"


# --- tool definitions -----------------------------------------------------------------


def test_parse_docstring():
    doc = """Do a thing.

    More about it, over
    two lines.

    Args:
        query: What to look for,
            in any language.
        k (int): How many.

    Returns:
        The ``things``.

    Raises:
        ToolError: Never.
    """
    desc, args, ret = parse_docstring(doc)
    assert desc == "Do a thing.\n\nMore about it, over two lines."
    assert args == {"query": "What to look for, in any language.", "k": "How many."}
    assert ret == "The `things`."


def test_every_core_tool_has_a_flat_definition():
    specs = tool_specs()
    assert list(specs) == list(CORE_TOOLS)
    for spec in specs.values():
        d = spec.definition()
        assert d["type"] == "function" and d["function"]["name"] == spec.name
        assert d["function"]["description"]
        text = json.dumps(d)
        assert "$ref" not in text and "$defs" not in text
        params = d["function"]["parameters"]
        assert params["type"] == "object" and params["additionalProperties"] is False
        for name, prop in params["properties"].items():
            assert prop.get("description"), (spec.name, name)


def test_definition_details():
    search = tool_specs(["search_publications"])["search_publications"]
    params = search.parameters()
    assert params["required"] == ["query"]
    assert params["properties"]["k"]["default"] == 8
    filters = params["properties"]["filters"]["anyOf"][0]
    assert filters["type"] == "object" and "year_from" in filters["properties"]
    count = tool_specs(["count_by"])["count_by"].parameters()
    assert "sub_area" in count["properties"]["row"]["enum"]
    assert "Returns:" in tool_specs(["count_by"])["count_by"].description
    with pytest.raises(ValueError, match="unknown tools"):
        tool_specs(["drop_tables"])


def test_build_spec_needs_type_hints():
    def untyped(x):  # type: ignore[no-untyped-def]
        """Nothing."""

    with pytest.raises(TypeError, match="no type hint"):
        build_spec(untyped)


# --- running a tool call --------------------------------------------------------------


def _spec_of(fn):  # type: ignore[no-untyped-def]
    return build_spec(fn)


def test_run_tool_validates_and_converts_arguments():
    seen: dict[str, Any] = {}

    def tool(row: Dimension, filters: Filters | None = None, k: int = 3) -> dict:
        """Echo.

        Args:
            row: A dimension.
            filters: Filters.
            k: Number.
        """
        seen.update(row=row, filters=filters, k=k)
        return {"ok": True}

    spec = _spec_of(tool)
    out = run_tool(
        spec, '{"row": "sub_area", "filters": {"year_from": 2010}}', max_chars=1000
    )
    assert out.ok and out.content == '{"ok":true}'
    assert seen["row"] is Dimension.SUB_AREA
    assert isinstance(seen["filters"], Filters) and seen["filters"].year_from == 2010
    assert seen["k"] == 3

    for raw, message in [
        ("{not json", "not valid JSON"),
        ("[1]", "JSON object"),
        ('{"row": "nope"}', "row"),
        ('{"row": "sdg", "extra": 1}', "extra"),
        ("{}", "row"),
    ]:
        bad = run_tool(spec, raw, max_chars=1000)
        assert not bad.ok and message in (bad.error or ""), raw
        assert json.loads(bad.content)["error"] == bad.error


def test_run_tool_reports_tool_errors():
    def refuse(x: str) -> None:
        """Refuse.

        Args:
            x: Anything.
        """
        raise ToolError("no such record; use search_publications")

    def missing(x: str) -> None:
        """Missing.

        Args:
            x: Anything.
        """
        raise NotImplementedError

    def crash(x: str) -> None:
        """Crash.

        Args:
            x: Anything.
        """
        raise RuntimeError("secret internals")

    assert run_tool(_spec_of(refuse), '{"x": "a"}', max_chars=500).error == (
        "no such record; use search_publications"
    )
    assert "not available yet" in (
        run_tool(_spec_of(missing), '{"x": "a"}', max_chars=500).error or ""
    )
    crashed = run_tool(_spec_of(crash), '{"x": "a"}', max_chars=500)
    assert "RuntimeError" in (crashed.error or "")
    assert "secret" not in crashed.content


def test_compact_and_render():
    data = {
        "cells": [{"row": "A", "record_ids": [f"r{i}" for i in range(30)]}],
        "provenance": {"model": "x"},
        "nested": [{"provenance": 1, "keep": 2}],
    }
    small = compact(data, omit_fields=["provenance"], max_ids=5)
    assert small["cells"][0]["record_ids"] == ["r0", "r1", "r2", "r3", "r4"]
    assert small["cells"][0]["record_ids_omitted"] == 25
    assert "provenance" not in small and small["nested"] == [{"keep": 2}]
    text = render({"text": "x" * 5000}, max_chars=600)
    assert len(text) < 800 and "cut: the result had" in text


# --- agent loop -----------------------------------------------------------------------


def call(name: str, args: dict, cid: str = "c1") -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def reply(content: str = "", calls: list | None = None, finish: str = "stop") -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
        finish = "tool_calls"
    return {
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


class ScriptedLLM:
    """Replays assistant replies in order and records every request."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(json.loads(json.dumps(body)))
        if not self.replies:
            return httpx.Response(500, text="script ended")
        return httpx.Response(200, json=self.replies.pop(0))


def make_config(tmp_path: Path, db: Path, extra: str = "") -> Config:
    path = tmp_path / "o.yaml"
    path.write_text(
        f'data:\n  catalogue: "{db.as_posix()}"\nlogging:\n  file: null\n' + extra,
        encoding="utf-8",
    )
    return load_config(path)


def make_agent(cfg: Config, llm: ScriptedLLM, **kw: Any) -> Agent:
    client = EndpointClient(
        "https://llm.test/v1",
        "k",
        timeout_s=5,
        max_retries=0,
        transport=httpx.MockTransport(llm),
        sleep=lambda _: None,
    )
    return Agent(cfg, client, **kw)


def test_answers_after_a_tool_call(fixture_db: Path, tmp_path: Path):
    cfg = make_config(tmp_path, fixture_db)
    llm = ScriptedLLM(
        [
            reply(calls=[call("get_publication", {"record_id": "10.1659/mrd.4204"})]),
            reply(f"It is an editorial from 2022 [BORIS:{MRD}, p. 2]."),
        ]
    )
    seen: list[str] = []
    agent = make_agent(cfg, llm, on_tool_call=lambda r: seen.append(r.name))
    result = agent.ask("What is 10.1659/mrd.4204?")

    assert result.answer.startswith("It is an editorial")
    assert result.stop_reason == "answered" and result.rounds == 2
    assert [t.name for t in result.tool_calls] == ["get_publication"] == seen
    assert result.tool_calls[0].ok
    [cite] = result.citations
    assert cite.citation.record_id == MRD and cite.pages == [2] and cite.from_tools
    assert cite.citation.doi == "10.1659/mrd.4204"
    assert result.unknown_ids == []
    assert result.usage["total_tokens"] == 220
    assert result.model == cfg.models.agent
    assert result.prompt_version == cfg.agent.system_prompt
    assert result.vocab_version == cfg.vocab.version

    first, second = llm.requests
    assert first["model"] == cfg.models.agent
    assert first["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in first["tools"]} == set(cfg.agent.tools)
    assert first["messages"][0]["role"] == "system"
    assert str(cfg.agent.max_tool_calls) in first["messages"][0]["content"]
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    content = json.loads(tool_msg["content"])
    assert content["record"]["record_id"] == MRD
    assert "provenance" not in json.dumps(content)  # agent.omit_fields


def test_tool_errors_go_back_to_the_model(fixture_db: Path, tmp_path: Path):
    llm = ScriptedLLM(
        [
            reply(
                calls=[
                    call("get_publication", {"record_id": "nope"}, "a"),
                    call("delete_everything", {}, "b"),
                    call("count_by", {"row": "colour"}, "c"),
                ]
            ),
            reply("I could not find it."),
        ]
    )
    result = make_agent(make_config(tmp_path, fixture_db), llm).ask("?")
    assert [t.ok for t in result.tool_calls] == [False, False, False]
    msgs = llm.requests[1]["messages"]
    errors = [json.loads(m["content"])["error"] for m in msgs if m["role"] == "tool"]
    assert "search_publications" in errors[0]
    assert "unknown tool" in errors[1]
    assert "invalid arguments for count_by" in errors[2]


def test_budget_stops_tool_calls(fixture_db: Path, tmp_path: Path):
    cfg = make_config(tmp_path, fixture_db, "agent:\n  max_tool_calls: 2\n")
    vocab = call("list_vocabulary", {"dimension": "impact_area"})
    llm = ScriptedLLM(
        [
            reply(calls=[vocab]),
            reply(calls=[vocab, {**vocab, "id": "c2"}]),  # second call is over budget
            reply("Impact areas: JE, SL, SG, TE."),
        ]
    )
    result = make_agent(cfg, llm).ask("Which impact areas exist?")
    assert result.stop_reason == "budget"
    assert [t.ok for t in result.tool_calls] == [True, True, False]
    assert result.tool_calls[-1].error == "budget"
    final = llm.requests[-1]
    assert "tools" not in final  # no more tools offered
    assert final["messages"][-1] == {"role": "user", "content": BUDGET_NOTE}
    assert result.answer.startswith("Impact areas")


def test_citations_are_checked(fixture_db: Path, tmp_path: Path):
    llm = ScriptedLLM(
        [
            reply(
                f"Two sources [BORIS:{MRD}; BORIS:fixture-synthetic-04] and an "
                "invented one [BORIS:00000000-dead-beef]."
            )
        ]
    )
    result = make_agent(make_config(tmp_path, fixture_db), llm).ask("?")
    assert [c.citation.record_id for c in result.citations] == [
        MRD,
        "fixture-synthetic-04",
    ]
    assert not any(c.from_tools for c in result.citations)  # no tool was called
    assert result.unknown_ids == ["00000000-dead-beef"]


def test_ids_from_counts_count_as_seen(fixture_db: Path, tmp_path: Path):
    llm = ScriptedLLM(
        [
            reply(calls=[call("count_by", {"row": "impact_area"})]),
            reply(f"SG is common [BORIS:{MRD}]."),
        ]
    )
    result = make_agent(make_config(tmp_path, fixture_db), llm).ask("?")
    assert result.citations[0].from_tools


def test_conversation_keeps_history_and_reset(fixture_db: Path, tmp_path: Path):
    llm = ScriptedLLM([reply("First."), reply("Second.")])
    agent = make_agent(make_config(tmp_path, fixture_db), llm)
    agent.ask("one")
    agent.ask("two")
    roles = [m["role"] for m in llm.requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    agent.reset()
    assert [m["role"] for m in agent.messages] == ["system"]


def test_length_and_bad_replies(fixture_db: Path, tmp_path: Path):
    cfg = make_config(tmp_path, fixture_db)
    llm = ScriptedLLM([reply("Partial answ", finish="length")])
    assert make_agent(cfg, llm).ask("?").stop_reason == "length"
    with pytest.raises(AgentError, match="no message"):
        make_agent(cfg, ScriptedLLM([{"choices": []}])).ask("?")


def test_extra_body_and_model_override(fixture_db: Path, tmp_path: Path):
    cfg = make_config(
        tmp_path,
        fixture_db,
        "agent:\n  extra_body:\n    reasoning_effort: low\n"
        "  tools: [get_publication]\n",
    )
    llm = ScriptedLLM([reply("Hi.")])
    make_agent(cfg, llm, model="qwen-alt").ask("?")
    req = llm.requests[0]
    assert req["reasoning_effort"] == "low" and req["model"] == "qwen-alt"
    assert [t["function"]["name"] for t in req["tools"]] == ["get_publication"]


# --- citation helpers -----------------------------------------------------------------


def test_parse_citations():
    text = (
        "A [BORIS:abc-1, p. 4; BORIS: def_2] and [BORIS:fixture-synthetic-04, pp. 2-3]."
        " Not a citation [1] or (BORIS) or [see BORIS]."
    )
    assert parse_citations(text) == [
        ("abc-1", 4),
        ("def_2", None),
        ("fixture-synthetic-04", 2),
    ]


def test_record_ids_in():
    data = [
        {"record_id": "a", "x": {"record_ids": ["b", "c"]}},
        {"citation": {"record_id": "d"}},
    ]
    assert record_ids_in(data) == {"a", "b", "c", "d"}
