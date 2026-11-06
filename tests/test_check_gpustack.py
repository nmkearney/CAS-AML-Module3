"""Offline tests for scripts/check_gpustack.py: evaluation logic and a fake server."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from chatcde.config import load_config

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_gpustack.py"
spec = importlib.util.spec_from_file_location("check_gpustack", SCRIPT)
cg = importlib.util.module_from_spec(spec)
sys.modules["check_gpustack"] = cg  # dataclasses look the module up by name
spec.loader.exec_module(cg)


def tool_message(name="get_publication", args='{"boris_id": "boris-12345"}'):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": name, "arguments": args},
            }
        ],
    }


GOOD_TAGS = {
    "countries": ["LAO", "MMR"],
    "sdgs": [2, 15],
    "language": "de",
    "evidence": "Landnutzungskonflikte im Hochland von Laos und Myanmar",
}


# --- Evaluation -------------------------------------------------------------


def test_tool_call_valid_and_invalid():
    assert cg.evaluate_tool_call(tool_message())[0]
    ok, detail = cg.evaluate_tool_call({"content": "The title is X."})
    assert not ok and "no tool_calls" in detail
    assert not cg.evaluate_tool_call(tool_message(name="search"))[0]
    assert not cg.evaluate_tool_call(tool_message(args="{boris_id: 1"))[0]
    assert not cg.evaluate_tool_call(tool_message(args='{"boris_id": "x"}'))[0]


def test_tool_answer():
    assert cg.evaluate_tool_answer({"content": "Land governance in the Mekong"})[0]
    assert not cg.evaluate_tool_answer({"content": ""})[0]
    assert not cg.evaluate_tool_answer(tool_message())[0]


def test_json_output():
    ok, detail = cg.evaluate_json_output(json.dumps(GOOD_TAGS))
    assert ok and "countries right" in detail
    ok, detail = cg.evaluate_json_output(
        "Here:\n```json\n" + json.dumps(GOOD_TAGS) + "\n```"
    )
    assert not ok and "wrapped" in detail
    ok, detail = cg.evaluate_json_output(json.dumps(GOOD_TAGS | {"sdgs": [1, 2, 3, 4]}))
    assert not ok and "sdgs" in detail
    assert not cg.evaluate_json_output(json.dumps({"country": "LAO"}))[0]
    assert not cg.evaluate_json_output("no json here")[0]


class ReasoningOnlyClient(cg.Client):
    """Thinks until the token budget runs out unless thinking is switched off."""

    def __init__(self):
        super().__init__("https://gpu.example/v1", "key", 5)

    def _send(self, method, url, payload):
        nothink = (
            payload.get("chat_template_kwargs", {}).get("enable_thinking") is False
        )
        if nothink or payload["response_format"]["type"] == "json_object":
            msg, finish = {"content": json.dumps(GOOD_TAGS)}, "stop"
        else:
            msg, finish = {"content": None, "reasoning": "x" * 5000}, "length"
        body = {
            "choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"completion_tokens": 2048},
        }
        return 200, json.dumps(body)


def test_json_schema_reports_reasoning_exhaustion():
    results = {
        r.check: r for r in cg.check_json_schema(ReasoningOnlyClient(), "tagging", "m")
    }
    main = results["json_schema"]
    assert not main.ok and "finish=length" in main.detail
    assert main.raw["reasoning_chars"] == 5000
    assert results["json_schema_nothink"].ok
    assert not results["json_schema_nothink"].required
    assert results["json_object"].ok


def test_embeddings():
    good = {
        "data": [
            {"index": 0, "embedding": [1.0, 0.0]},
            {"index": 1, "embedding": [0.9, 0.1]},
            {"index": 2, "embedding": [0.0, 1.0]},
        ]
    }
    ok, detail = cg.evaluate_embeddings(good)
    assert ok and "dim 2" in detail
    swapped = {
        "data": [
            good["data"][0],
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 2, "embedding": [0.9, 0.1]},
        ]
    }
    assert not cg.evaluate_embeddings(swapped)[0]
    assert not cg.evaluate_embeddings({"data": good["data"][:2]})[0]


def test_rerank():
    body = {
        "results": [
            {"index": 0, "relevance_score": 0.1},
            {"index": 1, "relevance_score": 0.9},
            {"index": 2, "relevance_score": 0.2},
        ]
    }
    assert cg.evaluate_rerank(body)[0]
    body["results"][0]["relevance_score"] = 0.95
    assert not cg.evaluate_rerank(body)[0]
    assert not cg.evaluate_rerank({})[0]


def level(parallel, rps, errors=0):
    return cg.ConcurrencyLevel(
        parallel, 2 * parallel, errors, 1.0, rps, None, None, None
    )


def test_recommend_concurrency():
    assert (
        cg.recommend_concurrency(
            [level(1, 1), level(2, 2), level(4, 3.9), level(8, 4.2)]
        )
        == 4
    )
    assert (
        cg.recommend_concurrency([level(1, 1), level(2, 2), level(4, 4, errors=1)]) == 2
    )
    assert cg.recommend_concurrency([level(1, 1, errors=2)]) == 1


def test_parse_levels():
    assert cg.parse_levels("4,1,2") == [1, 2, 4]
    with pytest.raises(Exception, match="between"):
        cg.parse_levels("1,64")


def test_tiny_png_is_png():
    assert cg.tiny_png().startswith(b"\x89PNG\r\n\x1a\n")


# --- Fake server --------------------------------------------------------------


class FakeClient(cg.Client):
    """Answers like a well-behaved OpenAI-compatible server; records the calls."""

    def __init__(self, served, html=False):
        super().__init__("https://gpu.example/v1", "key", 5)
        self.served = served
        self.html = html
        self.calls = []

    def _send(self, method, url, payload):
        self.calls.append((method, url, payload))
        if self.html:
            return 200, "<html>Temporary Access Restriction</html>"
        if url.endswith("/models"):
            return 200, json.dumps({"data": [{"id": m} for m in self.served]})
        if url.endswith("/embeddings"):
            vecs = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
            return 200, json.dumps(
                {"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]}
            )
        if url == "https://gpu.example/v1/rerank":
            return 404, '{"error": "not found"}'
        if url.endswith("/rerank"):
            return 200, json.dumps(
                {
                    "results": [
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.1},
                    ]
                }
            )
        # chat/completions
        if "tools" in payload:
            if payload["messages"][-1]["role"] == "tool":
                msg = {"content": "Land governance in the Mekong region (2021)."}
            else:
                msg = tool_message()
        elif "response_format" in payload:
            msg = {"content": json.dumps(GOOD_TAGS)}
        else:
            msg = {"content": "Soil is alive."}
        body = {"choices": [{"message": msg}], "usage": {"completion_tokens": 5}}
        return 200, json.dumps(body)


def all_models(cfg):
    names = list(cfg.models.model_dump().values())
    return names + [m for m in cfg.model_alternatives.model_dump().values() if m]


def test_run_checks_against_fake_server():
    cfg = load_config()
    client = FakeClient(all_models(cfg))
    only = set(cg.CHECKS) - {"vision"}
    results, levels = cg.run_checks(client, cfg, only, levels=[1, 2])
    failed = [r for r in results if r.required and not r.ok]
    assert not failed, failed
    checks = {r.check for r in results}
    assert {
        "models",
        "tool_call",
        "tool_answer",
        "tool_required",
        "json_schema",
        "json_schema_nothink",
        "embedding",
        "rerank",
        "concurrency",
    } <= checks
    assert [lv.parallel for lv in levels] == [1, 2]
    rerank = next(r for r in results if r.check == "rerank")
    assert "url https://gpu.example/rerank" in rerank.detail  # fell back past /v1
    # both agent models were asked for a tool call
    agents = {r.model for r in results if r.check == "tool_call"}
    assert agents == {cfg.models.agent, cfg.model_alternatives.agent}


def test_missing_model_is_reported():
    cfg = load_config()
    client = FakeClient([cfg.models.agent])
    results, _ = cg.run_checks(client, cfg, {"models"})
    assert not results[0].ok and cfg.models.tagging in results[0].detail


def test_html_page_is_an_access_error():
    cfg = load_config()
    with pytest.raises(cg.AccessError, match="HTML"):
        cg.run_checks(FakeClient([], html=True), cfg, {"models"})


def test_main_without_key_exits_2(monkeypatch, capsys):
    cfg = load_config()
    monkeypatch.delenv(cfg.llm.api_key_env, raising=False)
    assert cg.main(["--only", "models"]) == 2


def test_report_has_no_key(tmp_path):
    cfg = load_config()
    client = FakeClient(all_models(cfg))
    results, levels = cg.run_checks(client, cfg, {"models", "embedding"})
    path = cg.write_report(tmp_path, cfg, results, levels)
    text = path.read_text()
    assert json.loads(text)["base_url"] == cfg.llm.base_url
    assert "Bearer" not in text


@pytest.mark.network
def test_live_endpoint(tmp_path):
    cfg = load_config()
    if not os.environ.get(cfg.llm.api_key_env):
        pytest.skip(f"{cfg.llm.api_key_env} not set")
    assert cg.main(["--only", "models", "--out", str(tmp_path)]) == 0
