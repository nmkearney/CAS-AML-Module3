#!/usr/bin/env python3
"""Check what the configured GPUStack models can do (issue #4).

Calls every model named in ``models`` and ``model_alternatives`` of the merged
ChatCDE config through the OpenAI-compatible API and reports what works:

* ``models``: ``GET /models`` answers JSON, the key is accepted, and every
  configured model is served.
* ``tool``: the agent models return a proper ``tool_calls`` entry for a
  ``get_publication`` tool, then answer in text once the tool result is sent back.
  ``tool_choice="required"`` is tried as well (informational).
* ``json``: the tagging models honour ``response_format`` with a JSON schema. The
  prompt does not spell out the field names, so a valid answer shows that the
  server enforces the schema. The input is a German abstract (multilingual check).
* ``embedding``: an English sentence lies closer to its German paraphrase than to
  an unrelated sentence; the vector size is recorded.
* ``rerank``: a German query ranks the matching English passage first. Reranking is
  not part of the OpenAI API, so ``llm.rerank_path`` is tried and, on 404, the
  same path without ``/v1``.
* ``concurrency``: batches of 1, 2, 4, 8 parallel requests to the tagging model;
  throughput, latency and errors per level, and a suggested ``llm.max_concurrency``.
* ``vision`` (only with ``--vision``): the vision models name the colour of a
  small generated image.

Nothing is hard-coded: URL, key variable, timeouts and model names come from the
config (set ``llm.base_url`` in ``~/.config/chatcde/config.yaml`` and export the key
variable named by ``llm.api_key_env``). The key is never printed or saved.

A table goes to stdout. A JSON report with every result, a short excerpt of each
raw response and the code version goes to ``<data.dir>/checks/`` (gitignored).
Copy the findings into docs/decisions/0003-models-by-role.md.

    python scripts/check_gpustack.py                    # all checks except vision
    python scripts/check_gpustack.py --only models,tool # a subset
    python scripts/check_gpustack.py --vision --concurrency 1,2,4

Exit code: 0 if every required check passed, 1 if one failed, 2 if the endpoint
cannot be used at all (no key, HTML page instead of JSON, config error).
Uses only the standard library plus ChatCDE's own dependencies.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import re
import ssl
import statistics
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rich.console import Console
from rich.table import Table

import chatcde
from chatcde.config import Config, ConfigError, load_config, project_root
from chatcde.log import setup_logging

try:  # python.org builds on macOS ship without root certificates
    import certifi

    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

log = logging.getLogger("check_gpustack")

CHECKS = ("models", "tool", "json", "embedding", "rerank", "concurrency", "vision")
"""All check names, in run order. ``vision`` only runs with ``--vision``."""

MAX_PARALLEL = 16
"""Upper bound for ``--concurrency`` levels: GPUStack is a shared cluster."""

EXCERPT_CHARS = 1500
"""Raw responses are cut to this length in the JSON report."""

REASONING_TOKENS = 2048
"""``max_tokens`` for checks where reasoning models may think before answering."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Outcome of one check against one model.

    ``required`` checks decide the exit code; the others are for information
    (for example whether ``tool_choice="required"`` is supported).
    """

    check: str
    role: str
    model: str
    ok: bool
    detail: str
    latency_s: float | None = None
    required: bool = True
    raw: Any = None


@dataclass
class ConcurrencyLevel:
    """Measurements for one batch of parallel requests."""

    parallel: int
    requests: int
    errors: int
    wall_s: float
    requests_per_s: float
    p50_s: float | None
    p95_s: float | None
    completion_tokens_per_s: float | None
    error_kinds: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class AccessError(Exception):
    """The endpoint cannot be used: HTML instead of JSON, bad key, unreachable."""


class HttpError(Exception):
    """A request returned an HTTP error status."""

    def __init__(self, status: int, body: str) -> None:
        """Keep the status and the start of the body for the report."""
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class Client:
    """Minimal JSON client for an OpenAI-compatible endpoint.

    All traffic goes through :meth:`_send`, so tests can replace it.
    """

    def __init__(self, base_url: str, api_key: str, timeout_s: float) -> None:
        """Store the endpoint; ``base_url`` usually ends in ``/v1``."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def url(self, path: str) -> str:
        """Return ``path`` joined to the base URL, or ``path`` if it is a full URL."""
        if path.startswith(("http://", "https://")):
            return path
        return self.base_url + "/" + path.lstrip("/")

    def _send(self, method: str, url: str, payload: dict | None) -> tuple[int, str]:
        """Send one request; return ``(status, body)``, also for 4xx/5xx."""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"chatcde-check-gpustack/{chatcde.__version__}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout_s, context=SSL_CTX
            ) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[dict, float]:
        """Send a request and return ``(parsed JSON, seconds taken)``.

        Raises:
            AccessError: The body is HTML (e.g. an access-restriction page sent
                with HTTP 200) or the key is rejected (401/403).
            HttpError: Any other non-2xx status.
        """
        start = time.perf_counter()
        status, body = self._send(method, self.url(path), payload)
        elapsed = time.perf_counter() - start
        if body.lstrip().startswith("<"):
            raise AccessError(
                f"{self.url(path)} returned HTML instead of JSON (HTTP {status}). "
                "Check llm.base_url and whether you need the UniBe network or VPN."
            )
        if status in (401, 403):
            raise AccessError(f"HTTP {status}: the API key was rejected. {body[:200]}")
        if not 200 <= status < 300:
            raise HttpError(status, body)
        try:
            return json.loads(body), elapsed
        except json.JSONDecodeError as exc:
            raise HttpError(status, f"not JSON: {body[:300]}") from exc

    def get(self, path: str) -> tuple[dict, float]:
        """GET ``path``; see :meth:`request`."""
        return self.request("GET", path)

    def post(self, path: str, payload: dict) -> tuple[dict, float]:
        """POST ``payload`` to ``path``; see :meth:`request`."""
        return self.request("POST", path, payload)


def excerpt(obj: Any) -> Any:
    """Return ``obj`` for the report, as a cut string if its JSON is long."""
    text = json.dumps(obj, ensure_ascii=False)
    return obj if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + " …"


def first_message(body: dict) -> dict:
    """Return ``choices[0].message`` of a chat completion, or ``{}``."""
    choices = body.get("choices") or [{}]
    return choices[0].get("message") or {}


# ---------------------------------------------------------------------------
# Evaluation (pure functions; unit-tested without a server)
# ---------------------------------------------------------------------------

TOOL_NAME = "get_publication"
TOOL_ID = "boris-12345"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Return the metadata of one publication by its BORIS ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "boris_id": {"type": "string", "description": "The BORIS ID."}
                },
                "required": ["boris_id"],
                "additionalProperties": False,
            },
        },
    }
]
TOOL_PROMPT = (
    f"What is the title and year of the publication with BORIS ID '{TOOL_ID}'? "
    "Look it up with the tool; do not guess."
)
TOOL_RESULT = {
    "boris_id": TOOL_ID,
    "title": "Land governance in the Mekong region",
    "year": 2021,
}


def evaluate_tool_call(message: dict) -> tuple[bool, str]:
    """Check that an assistant message calls ``get_publication`` correctly.

    Returns:
        ``(ok, detail)``. ``ok`` needs a ``tool_calls`` entry with the right
        function name and JSON arguments whose ``boris_id`` holds the asked ID.
    """
    calls = message.get("tool_calls") or []
    if not calls:
        text = (message.get("content") or "").strip()
        return False, f"no tool_calls; text instead: {text[:120]!r}"
    fn = calls[0].get("function") or {}
    if fn.get("name") != TOOL_NAME:
        return False, f"called {fn.get('name')!r} instead of {TOOL_NAME!r}"
    try:
        args = json.loads(fn.get("arguments") or "")
    except json.JSONDecodeError:
        return False, f"arguments are not JSON: {fn.get('arguments')!r}"
    if not isinstance(args, dict) or "12345" not in str(args.get("boris_id", "")):
        return False, f"wrong arguments: {args!r}"
    extra = f"; {len(calls)} calls" if len(calls) > 1 else ""
    return True, f"tool_calls ok, arguments {json.dumps(args)}{extra}"


def evaluate_tool_answer(message: dict) -> tuple[bool, str]:
    """Check the answer after the tool result was sent back.

    ``ok`` needs plain text and no further tool call; whether the title from the
    tool result appears is reported in ``detail``.
    """
    if message.get("tool_calls"):
        return False, "called a tool again instead of answering"
    text = (message.get("content") or "").strip()
    if not text:
        return False, "empty answer"
    used = "uses the tool result" if "Mekong" in text else "tool result not quoted"
    return True, f"text answer, {used}: {text[:100]!r}"


class TagSample(BaseModel):
    """Small stand-in for the tagging output, used as the JSON schema to enforce."""

    model_config = ConfigDict(extra="forbid")

    countries: list[str] = Field(description="ISO 3166-1 alpha-3 codes")
    sdgs: list[int] = Field(description="Up to 3 SDG numbers, 1-17", max_length=3)
    language: Literal["en", "de", "fr", "es"]
    evidence: str = Field(description="A short quote from the text")


JSON_TEXT = (
    "Die Studie untersucht Landnutzungskonflikte im Hochland von Laos und Myanmar. "
    "Gestützt auf Haushaltsbefragungen zeigt sie, wie unsichere Landrechte und "
    "Konzessionen für Kautschukplantagen die Ernährungssicherheit kleinbäuerlicher "
    "Haushalte gefährden."
)
JSON_PROMPT = (
    "Tag this abstract: the countries it studies (ISO 3166-1 alpha-3), up to three "
    "Sustainable Development Goals, its language code, and a short evidence quote.\n\n"
    + JSON_TEXT
)
EXPECTED_COUNTRIES = {"LAO", "MMR"}


def evaluate_json_output(content: str | None) -> tuple[bool, str]:
    """Check that ``content`` is bare JSON that validates against :class:`TagSample`.

    JSON wrapped in prose or a code fence fails (the schema was not enforced),
    but the detail says so, so a fallback can be judged.
    """
    text = (content or "").strip()
    try:
        data = json.loads(text)
        wrapped = False
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return False, f"not JSON: {text[:120]!r}"
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return False, f"not JSON: {text[:120]!r}"
        wrapped = True
    try:
        tags = TagSample.model_validate(data)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
        return False, f"JSON does not match the schema ({fields}): {text[:120]!r}"
    if wrapped:
        return False, "matches the schema but is wrapped in other text"
    got = {c.upper() for c in tags.countries}
    content_note = (
        "countries right"
        if EXPECTED_COUNTRIES <= got
        else f"countries {sorted(got)} (expected {sorted(EXPECTED_COUNTRIES)})"
    )
    return True, f"valid against schema; {content_note}, language={tags.language}"


EMBED_INPUTS = [
    "Soil erosion threatens smallholder farms on steep mountain slopes.",
    "Bodenerosion bedroht kleinbäuerliche Betriebe an steilen Berghängen.",
    "The central bank raised interest rates to curb inflation.",
]


def cosine(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity of two vectors (0 if either is all zeros)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def evaluate_embeddings(body: dict) -> tuple[bool, str]:
    """Check an ``/embeddings`` answer for :data:`EMBED_INPUTS`.

    ``ok`` needs three vectors of equal size, with the EN-DE pair closer than
    EN-unrelated.
    """
    items = sorted(body.get("data") or [], key=lambda d: d.get("index", 0))
    vectors = [d.get("embedding") or [] for d in items]
    if len(vectors) != len(EMBED_INPUTS) or not all(vectors):
        return False, f"expected {len(EMBED_INPUTS)} vectors, got {len(vectors)}"
    dims = {len(v) for v in vectors}
    if len(dims) != 1:
        return False, f"vectors differ in size: {sorted(dims)}"
    same, other = cosine(vectors[0], vectors[1]), cosine(vectors[0], vectors[2])
    ok = same > other
    verdict = "EN-DE closer" if ok else "EN-DE NOT closer"
    return (
        ok,
        f"dim {dims.pop()}; cos EN-DE {same:.3f}, EN-other {other:.3f} ({verdict})",
    )


RERANK_QUERY = "Bodenerosion in Bergregionen"
RERANK_DOCS = [
    "Interest rates and inflation in Switzerland, 1990-2020.",
    "Soil erosion on steep cropland in the Ethiopian highlands.",
    "Gender roles in urban labour markets.",
]
RERANK_RELEVANT = 1


def evaluate_rerank(body: dict) -> tuple[bool, str]:
    """Check that a rerank answer puts :data:`RERANK_RELEVANT` first.

    Accepts the Jina/Cohere shape ``{"results": [{"index", "relevance_score"}]}``.
    """
    results = body.get("results") or body.get("data") or []
    if not results:
        return False, f"no results in answer: {str(body)[:120]}"
    ranked = sorted(
        results, key=lambda r: r.get("relevance_score", r.get("score", 0)), reverse=True
    )
    top = ranked[0].get("index")
    scores = ", ".join(
        f"{r.get('index')}:{r.get('relevance_score', r.get('score', 0)):.3f}"
        for r in ranked
    )
    return top == RERANK_RELEVANT, f"top index {top} (want {RERANK_RELEVANT}); {scores}"


def recommend_concurrency(levels: list[ConcurrencyLevel], gain: float = 1.2) -> int:
    """Pick the highest parallelism that still adds throughput without errors.

    Walks the levels from low to high and stops at the first that has errors or
    raises throughput by less than ``gain`` times the previous level. Uses
    completion tokens per second when the server reports usage, else requests
    per second.

    Returns:
        The suggested ``llm.max_concurrency`` (1 if nothing ran cleanly).
    """
    best, prev = 1, None
    for level in sorted(levels, key=lambda lv: lv.parallel):
        if level.errors:
            break
        rate = level.completion_tokens_per_s or level.requests_per_s
        if prev is not None and rate < prev * gain:
            break
        best, prev = level.parallel, rate
    return best


def percentile(values: list[float], q: float) -> float | None:
    """Return the ``q`` quantile (0-1) of ``values`` by nearest rank, or ``None``."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def tiny_png(rgb: tuple[int, int, int] = (220, 30, 30), size: int = 64) -> bytes:
    """Return a solid-colour PNG, built with the standard library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Checks (one request or a few each; errors become failed results)
# ---------------------------------------------------------------------------


def _failed(check: str, role: str, model: str, exc: Exception) -> CheckResult:
    """Turn an exception from a request into a failed result."""
    return CheckResult(check, role, model, False, f"{type(exc).__name__}: {exc}")


def check_models(client: Client, wanted: dict[str, str]) -> CheckResult:
    """List the served models and report which configured ones are missing.

    Args:
        wanted: Model name by label, e.g. ``{"agent": "gpt-oss-120b", ...}``.
    """
    body, latency = client.get("models")
    served = {m.get("id") for m in body.get("data") or []}
    missing = sorted({name for name in wanted.values() if name not in served})
    detail = f"{len(served)} served; " + (
        f"missing: {', '.join(missing)}" if missing else "all configured models served"
    )
    info = [m for m in body.get("data") or [] if m.get("id") in set(wanted.values())]
    return CheckResult(
        "models", "-", "-", not missing, detail, latency, raw=excerpt(info)
    )


def check_tool_call(client: Client, role: str, model: str) -> list[CheckResult]:
    """Ask for a tool call, send a fake result back, try ``tool_choice=required``."""
    results: list[CheckResult] = []
    messages: list[dict] = [
        {"role": "system", "content": "You answer questions using the tools given."},
        {"role": "user", "content": TOOL_PROMPT},
    ]
    base = {"model": model, "tools": TOOLS, "temperature": 0}
    try:
        body, latency = client.post(
            "chat/completions",
            base
            | {
                "messages": messages,
                "tool_choice": "auto",
                "max_tokens": REASONING_TOKENS,
            },
        )
    except HttpError as exc:
        return [_failed("tool_call", role, model, exc)]
    message = first_message(body)
    ok, detail = evaluate_tool_call(message)
    if message.get("reasoning_content") or message.get("reasoning"):
        detail += "; returns reasoning separately"
    results.append(
        CheckResult("tool_call", role, model, ok, detail, latency, raw=excerpt(body))
    )

    if ok:
        call = message["tool_calls"][0]
        follow = messages + [
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": [call],
            },
            {
                "role": "tool",
                "tool_call_id": call.get("id", "call_0"),
                "content": json.dumps(TOOL_RESULT),
            },
        ]
        try:
            body, latency = client.post(
                "chat/completions",
                base | {"messages": follow, "max_tokens": REASONING_TOKENS},
            )
            ok2, detail2 = evaluate_tool_answer(first_message(body))
            results.append(
                CheckResult(
                    "tool_answer", role, model, ok2, detail2, latency, raw=excerpt(body)
                )
            )
        except HttpError as exc:
            results.append(_failed("tool_answer", role, model, exc))

    try:
        body, latency = client.post(
            "chat/completions",
            base
            | {
                "messages": messages,
                "tool_choice": "required",
                "max_tokens": REASONING_TOKENS,
            },
        )
        ok3, detail3 = evaluate_tool_call(first_message(body))
        results.append(
            CheckResult(
                "tool_required",
                role,
                model,
                ok3,
                detail3,
                latency,
                required=False,
                raw=excerpt(body),
            )
        )
    except HttpError as exc:
        res = _failed("tool_required", role, model, exc)
        res.required = False
        results.append(res)
    return results


def chat_summary(body: dict) -> dict:
    """Return the parts of a chat completion worth keeping in the report.

    Long reasoning would push ``finish_reason`` and ``usage`` past the excerpt
    limit, so they are kept separately and only the reasoning length is recorded.
    """
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return {
        "finish_reason": choice.get("finish_reason"),
        "usage": body.get("usage"),
        "content": (message.get("content") or "")[:EXCERPT_CHARS],
        "reasoning_chars": len(reasoning),
        "reasoning_start": reasoning[:300],
    }


def _json_attempt(
    client: Client,
    check: str,
    role: str,
    model: str,
    payload: dict,
    required: bool,
) -> CheckResult:
    """Send one JSON request and evaluate it; ``detail`` ends with finish reason."""
    try:
        body, latency = client.post("chat/completions", payload)
    except HttpError as exc:
        res = _failed(check, role, model, exc)
        res.required = required
        return res
    summary = chat_summary(body)
    ok, detail = evaluate_json_output(first_message(body).get("content"))
    tokens = (summary["usage"] or {}).get("completion_tokens")
    detail += f"; finish={summary['finish_reason']}, {tokens} tokens"
    if summary["reasoning_chars"]:
        detail += f", reasoning {summary['reasoning_chars']} chars"
    return CheckResult(check, role, model, ok, detail, latency, required, summary)


def check_json_schema(client: Client, role: str, model: str) -> list[CheckResult]:
    """Request :class:`TagSample` as a JSON schema, then try two variants.

    * ``json_schema`` (required): the schema in ``response_format``.
    * ``json_schema_nothink`` (info): the same with ``enable_thinking: false``
      passed to the chat template; reasoning models such as Qwen3 may otherwise
      spend the whole token budget thinking. Models without the switch ignore it.
    * ``json_object`` (info, only if ``json_schema`` failed): plain JSON mode
      with the schema in the prompt, as a fallback.
    """
    schema = TagSample.model_json_schema()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": JSON_PROMPT}],
        "temperature": 0,
        "max_tokens": REASONING_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "tag_sample", "schema": schema, "strict": True},
        },
    }
    main = _json_attempt(client, "json_schema", role, model, payload, True)
    results = [main]
    nothink = payload | {"chat_template_kwargs": {"enable_thinking": False}}
    results.append(
        _json_attempt(client, "json_schema_nothink", role, model, nothink, False)
    )
    if not main.ok:
        prompt = (
            JSON_PROMPT
            + "\n\nAnswer with JSON matching this schema:\n"
            + json.dumps(schema)
        )
        fallback = payload | {
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }
        results.append(
            _json_attempt(client, "json_object", role, model, fallback, False)
        )
    return results


def check_embedding(client: Client, role: str, model: str) -> CheckResult:
    """Embed :data:`EMBED_INPUTS` and compare the similarities."""
    try:
        body, latency = client.post(
            "embeddings", {"model": model, "input": EMBED_INPUTS}
        )
    except HttpError as exc:
        return _failed("embedding", role, model, exc)
    ok, detail = evaluate_embeddings(body)
    usage = body.get("usage")
    return CheckResult(
        "embedding",
        role,
        model,
        ok,
        detail,
        latency,
        raw={"usage": usage, "model": body.get("model")},
    )


def rerank_urls(client: Client, path: str) -> list[str]:
    """Return rerank URLs to try: ``base_url + path``, then the same without ``/v1``."""
    urls = [client.url(path)]
    root = re.sub(r"/v1$", "", client.base_url)
    if root != client.base_url:
        urls.append(root + "/" + path.lstrip("/"))
    return urls


def check_rerank(client: Client, role: str, model: str, path: str) -> CheckResult:
    """Rerank :data:`RERANK_DOCS` for :data:`RERANK_QUERY`, trying the known paths."""
    payload = {
        "model": model,
        "query": RERANK_QUERY,
        "documents": RERANK_DOCS,
        "top_n": len(RERANK_DOCS),
    }
    last: Exception | None = None
    for url in rerank_urls(client, path):
        try:
            body, latency = client.post(url, payload)
        except HttpError as exc:
            last = exc
            if exc.status == 404:
                continue
            break
        ok, detail = evaluate_rerank(body)
        return CheckResult(
            "rerank",
            role,
            model,
            ok,
            f"{detail}; url {url}",
            latency,
            raw=excerpt(body),
        )
    assert last is not None
    return _failed("rerank", role, model, last)


def check_vision(client: Client, role: str, model: str) -> CheckResult:
    """Send a small red image and ask for its colour."""
    image = base64.b64encode(tiny_png()).decode("ascii")
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 64,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What colour is this image? Answer in one word.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image}"},
                    },
                ],
            }
        ],
    }
    try:
        body, latency = client.post("chat/completions", payload)
    except HttpError as exc:
        return _failed("vision", role, model, exc)
    text = (first_message(body).get("content") or "").strip()
    ok = "red" in text.lower()
    return CheckResult(
        "vision", role, model, ok, f"answer {text[:60]!r}", latency, raw=excerpt(body)
    )


def run_level(client: Client, model: str, parallel: int) -> ConcurrencyLevel:
    """Send ``2 * parallel`` short chat requests, ``parallel`` at a time."""
    n = 2 * parallel
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "Write one sentence about soil."}],
    }

    def one(_: int) -> tuple[float | None, int, str | None]:
        try:
            body, latency = client.post("chat/completions", payload)
        except (HttpError, OSError) as exc:
            kind = (
                f"HTTP {exc.status}"
                if isinstance(exc, HttpError)
                else type(exc).__name__
            )
            return None, 0, kind
        tokens = (body.get("usage") or {}).get("completion_tokens") or 0
        return latency, tokens, None

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        outcomes = list(pool.map(one, range(n)))
    wall = time.perf_counter() - start
    latencies = [lat for lat, _, err in outcomes if err is None and lat is not None]
    tokens = sum(tok for _, tok, _ in outcomes)
    kinds: dict[str, int] = {}
    for _, _, err in outcomes:
        if err:
            kinds[err] = kinds.get(err, 0) + 1
    return ConcurrencyLevel(
        parallel=parallel,
        requests=n,
        errors=sum(kinds.values()),
        wall_s=round(wall, 2),
        requests_per_s=round(len(latencies) / wall, 3) if wall else 0.0,
        p50_s=round(statistics.median(latencies), 2) if latencies else None,
        p95_s=round(p, 2) if (p := percentile(latencies, 0.95)) is not None else None,
        completion_tokens_per_s=round(tokens / wall, 1) if tokens and wall else None,
        error_kinds=kinds,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def role_models(
    cfg: Config, roles: Iterable[str], alternatives: bool = True
) -> list[tuple[str, str]]:
    """Return ``(label, model)`` pairs for the given roles, first choice first.

    Labels are the role name, or ``role (alt)`` for the alternative.
    """
    pairs = []
    for role in roles:
        pairs.append((role, getattr(cfg.models, role)))
        alt = getattr(cfg.model_alternatives, role) if alternatives else None
        if alt:
            pairs.append((f"{role} (alt)", alt))
    return pairs


def run_checks(
    client: Client,
    cfg: Config,
    only: set[str],
    alternatives: bool = True,
    levels: list[int] | None = None,
) -> tuple[list[CheckResult], list[ConcurrencyLevel]]:
    """Run the selected checks in order and collect their results.

    Raises:
        AccessError: The endpoint cannot be used; later checks would all fail.
    """
    results: list[CheckResult] = []
    concurrency: list[ConcurrencyLevel] = []
    plan: list[
        tuple[str, str, Callable[[str, str], CheckResult | list[CheckResult]]]
    ] = []

    if "models" in only:
        wanted = dict(
            role_models(
                cfg,
                ["agent", "tagging", "embedding", "reranker", "vision", "speech"],
                alternatives,
            )
        )
        log.info("Listing served models")
        try:
            results.append(check_models(client, wanted))
        except HttpError as exc:
            results.append(_failed("models", "-", "-", exc))
    for label, model in role_models(cfg, ["agent"], alternatives):
        if "tool" in only:
            plan.append((label, model, lambda r, m: check_tool_call(client, r, m)))
    for label, model in role_models(cfg, ["tagging"], alternatives):
        if "json" in only:
            plan.append((label, model, lambda r, m: check_json_schema(client, r, m)))
    for label, model in role_models(cfg, ["embedding"], alternatives):
        if "embedding" in only:
            plan.append((label, model, lambda r, m: check_embedding(client, r, m)))
    for label, model in role_models(cfg, ["reranker"], alternatives):
        if "rerank" in only:
            plan.append(
                (
                    label,
                    model,
                    lambda r, m: check_rerank(client, r, m, cfg.llm.rerank_path),
                )
            )
    for label, model in role_models(cfg, ["vision"], alternatives):
        if "vision" in only:
            plan.append((label, model, lambda r, m: check_vision(client, r, m)))

    for label, model, fn in plan:
        log.info("Checking %s (%s)", model, label)
        try:
            out = fn(label, model)
        except OSError as exc:  # timeouts, resets; AccessError still propagates
            out = _failed("request", label, model, exc)
        results.extend(out if isinstance(out, list) else [out])

    if "concurrency" in only:
        model = cfg.models.tagging
        for parallel in levels or [1, 2, 4, 8]:
            log.info("Concurrency: %d parallel requests to %s", parallel, model)
            concurrency.append(run_level(client, model, parallel))
        best = recommend_concurrency(concurrency)
        results.append(
            CheckResult(
                "concurrency",
                "tagging",
                model,
                True,
                f"suggested llm.max_concurrency = {best} (levels "
                + ", ".join(
                    f"{lv.parallel}: {lv.requests_per_s} req/s, {lv.errors} errors"
                    for lv in concurrency
                )
                + ")",
            )
        )
    return results, concurrency


def code_version() -> str:
    """Return the git commit (with ``-dirty``) or the package version."""
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=project_root(),
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return chatcde.__version__


def write_report(
    out_dir: Path,
    cfg: Config,
    results: list[CheckResult],
    concurrency: list[ConcurrencyLevel],
) -> Path:
    """Write the JSON report and return its path. The API key is never included."""
    now = datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gpustack-{now:%Y%m%d-%H%M%S}.json"
    report = {
        "created": now.isoformat(timespec="seconds"),
        "code_version": code_version(),
        "base_url": cfg.llm.base_url,
        "rerank_path": cfg.llm.rerank_path,
        "models": cfg.models.model_dump(),
        "model_alternatives": cfg.model_alternatives.model_dump(),
        "results": [asdict(r) for r in results],
        "concurrency": [asdict(lv) for lv in concurrency],
        "suggested_max_concurrency": (
            recommend_concurrency(concurrency) if concurrency else None
        ),
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def print_table(
    results: list[CheckResult], concurrency: list[ConcurrencyLevel]
) -> None:
    """Print the results (and concurrency levels, if any) as Rich tables on stdout."""
    console = Console()
    table = Table(title="GPUStack check")
    for col in ("check", "role", "model", "ok", "latency", "detail"):
        table.add_column(col, overflow="fold")
    for r in results:
        mark = (
            "[green]yes[/]"
            if r.ok
            else ("[red]NO[/]" if r.required else "[yellow]no[/]")
        )
        lat = f"{r.latency_s:.1f}s" if r.latency_s is not None else ""
        table.add_row(r.check, r.role, r.model, mark, lat, r.detail)
    console.print(table)
    if concurrency:
        ct = Table(title="Concurrency (tagging model)")
        for col in (
            "parallel",
            "requests",
            "errors",
            "wall s",
            "req/s",
            "tok/s",
            "p50 s",
            "p95 s",
        ):
            ct.add_column(col, justify="right")
        for lv in concurrency:
            ct.add_row(
                *(
                    str(v) if v is not None else ""
                    for v in (
                        lv.parallel,
                        lv.requests,
                        lv.errors,
                        lv.wall_s,
                        lv.requests_per_s,
                        lv.completion_tokens_per_s,
                        lv.p50_s,
                        lv.p95_s,
                    )
                )
            )
        console.print(ct)


def parse_levels(text: str) -> list[int]:
    """Parse ``--concurrency`` (e.g. ``"1,2,4,8"``), capped at :data:`MAX_PARALLEL`."""
    levels = sorted({int(x) for x in text.split(",") if x.strip()})
    if not levels or levels[0] < 1 or levels[-1] > MAX_PARALLEL:
        raise argparse.ArgumentTypeError(f"levels must be between 1 and {MAX_PARALLEL}")
    return levels


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config", type=Path, help="explicit config file (as chatcde --config)"
    )
    p.add_argument(
        "--only",
        default=",".join(c for c in CHECKS if c != "vision"),
        help=f"comma-separated checks from: {', '.join(CHECKS)}",
    )
    p.add_argument("--vision", action="store_true", help="also check the vision models")
    p.add_argument(
        "--no-alternatives",
        action="store_true",
        help="check only the first-choice model of each role",
    )
    p.add_argument(
        "--concurrency",
        type=parse_levels,
        default=[1, 2, 4, 8],
        help=f"parallel levels to try, max {MAX_PARALLEL} (default 1,2,4,8)",
    )
    p.add_argument("--out", type=Path, help="report folder (default <data.dir>/checks)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the checks, print the table, write the report; return the exit code."""
    args = parse_args(argv)
    only = {c.strip() for c in args.only.split(",") if c.strip()}
    if args.vision:
        only.add("vision")
    unknown = only - set(CHECKS)
    if unknown:
        print(f"Unknown checks: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging("DEBUG" if args.verbose else cfg.logging.level, file=None)

    key = cfg.llm.api_key()
    if not key:
        log.error("No API key: set the environment variable %s.", cfg.llm.api_key_env)
        return 2
    client = Client(cfg.llm.base_url, key, cfg.llm.timeout_s)
    log.info("Endpoint %s", cfg.llm.base_url)
    try:
        results, concurrency = run_checks(
            client, cfg, only, not args.no_alternatives, args.concurrency
        )
    except AccessError as exc:
        log.error("%s", exc)
        return 2
    except OSError as exc:
        log.error("Cannot reach %s: %s", cfg.llm.base_url, exc)
        return 2

    print_table(results, concurrency)
    path = write_report(args.out or cfg.data.dir / "checks", cfg, results, concurrency)
    log.info("Report written to %s", path)
    failed = [r for r in results if r.required and not r.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
