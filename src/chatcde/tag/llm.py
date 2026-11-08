"""Structured JSON answers from the local LLM, validated with pydantic.

Tagging and vocabulary drafting ask the model in the ``tagging`` role for answers
that must fit a schema (CLAUDE.md: "Use structured JSON output"). This module
sends ``/chat/completions`` requests with ``response_format`` set to the JSON
schema of a pydantic model, so the server (vLLM on GPUStack) constrains the
answer, and validates the reply with the same model before anyone uses it.

The HTTP client is Track B's :class:`~chatcde.store.endpoint.EndpointClient`
(TLS via certifi; retries on timeouts, 429 and 5xx), used unchanged. Model names
come from the config; nothing here names a model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from chatcde.store.endpoint import EndpointClient, EndpointError

log = logging.getLogger(__name__)


class LLMReplyError(Exception):
    """The model's answer was cut off, empty or did not fit the schema."""


def load_prompt(prompts_dir: Path, name: str) -> str:
    """Read the versioned prompt ``<prompts_dir>/<name>.md``, e.g. ``vocab_extract.v1``.

    Copied from ``chatcde.parse.ocr`` (PR #42); share one helper once both are merged.

    Raises:
        FileNotFoundError: The prompt file does not exist.
    """
    return (prompts_dir / f"{name}.md").read_text(encoding="utf-8").strip()


def fill_prompt(template: str, values: Mapping[str, object]) -> str:
    """Replace ``{{name}}`` placeholders in a prompt template.

    Double braces, not ``str.format``, because prompts contain JSON examples
    with single braces.

    Raises:
        KeyError: A placeholder is left unfilled (a typo in the prompt or caller).
    """
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    if "{{" in out:
        start = out.index("{{")
        raise KeyError(f"unfilled placeholder {out[start : out.find('}}', start) + 2]}")
    return out


def _strip_fences(text: str) -> str:
    """Remove a Markdown code fence around the whole answer, if the model added one."""
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])
    return text.strip()


def chat_json[T: BaseModel](
    client: EndpointClient,
    *,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    max_tokens: int,
    extra_body: Mapping[str, Any] | None = None,
    temperature: float = 0.0,
    attempts: int = 2,
) -> T:
    """Ask ``model`` for an answer that fits ``schema`` and return it validated.

    Args:
        client: Endpoint client (safe to share between threads).
        model: Model name, read by the caller from the config.
        system: System message (the versioned prompt).
        user: User message (the record or cluster to work on).
        schema: Pydantic model the answer must fit; its JSON schema is sent as
            ``response_format`` so the server constrains decoding.
        max_tokens: Answer budget. An answer cut off at this limit is refused,
            because half a JSON object cannot be trusted.
        extra_body: Extra request fields from the config, e.g.
            ``chat_template_kwargs`` to turn off qwen3's thinking.
        temperature: Sampling temperature; 0 for repeatable answers.
        attempts: Tries in total when the answer is invalid. Endpoint failures
            are retried by the client, not here.

    Returns:
        The validated answer.

    Raises:
        LLMReplyError: Every attempt gave an invalid, empty or cut-off answer.
        chatcde.store.endpoint.EndpointError: The request itself failed.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": True,
            },
        },
    }
    payload.update(extra_body or {})
    problem = "no attempt made"
    for attempt in range(1, attempts + 1):
        body = client.post_json("/chat/completions", payload)
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            problem = "reply has no choices[0].message.content"
        else:
            if choice.get("finish_reason") == "length":
                problem = f"answer cut off at max_tokens={max_tokens}"
            elif not content.strip():
                problem = "empty answer"
            else:
                try:
                    return schema.model_validate_json(_strip_fences(content))
                except ValidationError as exc:
                    problem = f"answer does not fit {schema.__name__}: " + json.dumps(
                        exc.errors(include_url=False)[:3], default=str
                    )
        log.debug("attempt %d/%d: %s", attempt, attempts, problem)
    raise LLMReplyError(problem)


def run_parallel[I, R](
    items: Sequence[I],
    ask: Callable[[I], R],
    handle: Callable[[I, R | None, str | None], None],
    *,
    workers: int,
    max_consecutive_failures: int,
) -> bool:
    """Run ``ask`` on every item with up to ``workers`` requests in flight.

    ``handle(item, result, error)`` is called in the calling thread for each
    finished item, so it may write to DuckDB (connections are not shared between
    threads). ``error`` is ``"LLMReplyError: ..."`` or ``"EndpointError: ..."``
    and ``result`` is then None.

    An invalid answer is the model's fault and does not count towards stopping.
    After ``max_consecutive_failures`` endpoint errors in a row no new requests
    are sent; the ones in flight still finish.

    Returns:
        True if the run stopped early because the endpoint kept failing.
    """
    queue = list(items)
    consecutive = 0
    stopped = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: dict[Future[R], I] = {}
        while (queue and not stopped) or pending:
            while queue and not stopped and len(pending) < workers:
                item = queue.pop(0)
                pending[pool.submit(ask, item)] = item
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in ready:
                item = pending.pop(fut)
                try:
                    result = fut.result()
                except LLMReplyError as exc:
                    consecutive = 0
                    handle(item, None, f"LLMReplyError: {exc}")
                    continue
                except EndpointError as exc:
                    consecutive += 1
                    if consecutive >= max_consecutive_failures and not stopped:
                        stopped = True
                        log.error(
                            "%d endpoint failures in a row; stopping (last: %s)",
                            consecutive,
                            exc,
                        )
                    handle(item, None, f"EndpointError: {exc}")
                    continue
                consecutive = 0
                handle(item, result, None)
    return stopped
