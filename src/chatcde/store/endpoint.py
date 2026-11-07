"""Client for the local OpenAI-compatible endpoint (GPUStack, vLLM or Ollama).

Only what the store needs: ``/embeddings`` (issue #21) and ``/rerank`` (#22). The
endpoint, key variable, timeout and retries come from the config; model names
are passed in by the caller, which reads them from the ``models`` section, so no
model is hard-coded here.

* TLS is verified against ``certifi``'s roots, as for BORIS (python.org builds
  on macOS have none). Verification is never turned off.
* Timeouts, dropped connections, 429 and 5xx replies are retried with a growing
  pause (1, 2, 4 s ...); other errors fail at once with the server's message.
* The client is safe to share between threads, so callers can run up to
  ``llm.max_concurrency`` requests in parallel.
"""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import Callable, Sequence
from typing import Any

import certifi
import httpx

from chatcde.config import Config

log = logging.getLogger(__name__)


class EndpointError(Exception):
    """The endpoint refused a request or kept failing after all retries."""


class EndpointClient:
    """Minimal client for an OpenAI-compatible API.

    Args:
        base_url: API root, e.g. ``https://gpustack.unibe.ch/v1``.
        api_key: Bearer token; ``None`` sends no ``Authorization`` header
            (enough for a local Ollama).
        timeout_s: Timeout per request.
        max_retries: Retries after the first attempt for retryable failures.
        key_hint: Name of the environment variable holding the key, shown in
            the error message when the server answers 401.
        transport: Optional httpx transport, for tests (``httpx.MockTransport``).
        sleep: Function used to pause between retries; tests pass a no-op.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        timeout_s: float,
        max_retries: int,
        key_hint: str = "the API key variable",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Open an HTTP client that verifies TLS with certifi's roots."""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.key_hint = key_hint
        self._sleep = sleep
        self.http = httpx.Client(
            headers=headers,
            timeout=timeout_s,
            verify=ssl.create_default_context(cafile=certifi.where()),
            transport=transport,
        )

    @classmethod
    def from_config(
        cls, cfg: Config, *, max_retries: int | None = None
    ) -> EndpointClient:
        """Build a client from the ``llm`` section (API key from the environment)."""
        api_key = cfg.llm.api_key()
        if not api_key:
            log.warning(
                "%s is not set; sending requests without an API key",
                cfg.llm.api_key_env,
            )
        return cls(
            cfg.llm.base_url,
            api_key,
            timeout_s=cfg.llm.timeout_s,
            max_retries=cfg.embedding.max_retries
            if max_retries is None
            else max_retries,
            key_hint=cfg.llm.api_key_env,
        )

    def __enter__(self) -> EndpointClient:
        """Use the client in a ``with`` block."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the client at the end of a ``with`` block."""
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connections."""
        self.http.close()

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST ``payload`` to ``base_url + path`` and return the JSON reply.

        Raises:
            EndpointError: On a non-retryable error, a non-JSON reply, or when
                all retries are used up.
        """
        url = self.base_url + path
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.post(url, json=payload)
            except httpx.TransportError as exc:
                problem = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 401:
                    raise EndpointError(
                        f"401 Unauthorized from {url}; set {self.key_hint}"
                    )
                if r.status_code == 429 or r.status_code >= 500:
                    problem = f"HTTP {r.status_code}"
                elif r.is_error:
                    raise EndpointError(
                        f"HTTP {r.status_code} from {url}: {r.text[:300]}"
                    )
                else:
                    try:
                        body = r.json()
                    except ValueError as exc:
                        raise EndpointError(f"non-JSON reply from {url}") from exc
                    if not isinstance(body, dict):
                        raise EndpointError(f"unexpected reply from {url}")
                    return body
            if attempt == self.max_retries:
                raise EndpointError(
                    f"{problem} from {url} after {self.max_retries + 1} attempts"
                )
            pause = 2.0**attempt
            log.warning(
                "%s from %s; retry %d/%d in %.0f s",
                problem,
                url,
                attempt + 1,
                self.max_retries,
                pause,
            )
            self._sleep(pause)
        raise AssertionError("unreachable")  # pragma: no cover

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` with ``model``; one vector per text, in input order.

        Raises:
            EndpointError: If the request fails or the reply does not hold one
                vector per input.
        """
        body = self.post_json(
            "/embeddings",
            {"model": model, "input": list(texts), "encoding_format": "float"},
        )
        items = sorted(body.get("data") or [], key=lambda d: d.get("index", 0))
        vectors = [d.get("embedding") for d in items]
        if len(vectors) != len(texts) or not all(
            isinstance(v, list) and v for v in vectors
        ):
            raise EndpointError(
                f"expected {len(texts)} embeddings from {model}, got {len(vectors)}"
            )
        return vectors  # type: ignore[return-value]

    def rerank(
        self,
        model: str,
        query: str,
        documents: Sequence[str],
        *,
        path: str = "/rerank",
    ) -> list[tuple[int, float]]:
        """Score ``documents`` against ``query`` with a reranker model.

        Reranking is not part of the OpenAI API; GPUStack (vLLM) serves the
        Jina/Cohere format at ``/v1/rerank`` (decision 0003): the reply holds
        ``results`` with an ``index`` into ``documents`` and a ``relevance_score``.

        Args:
            model: Reranker model (the ``reranker`` role in the config).
            query: The search text.
            documents: Candidate passages.
            path: Route appended to the base URL (``llm.rerank_path``).

        Returns:
            ``(index, score)`` pairs, best first.

        Raises:
            EndpointError: If the request fails or the reply is malformed.
        """
        if not documents:
            return []
        body = self.post_json(
            path,
            {
                "model": model,
                "query": query,
                "documents": list(documents),
                "top_n": len(documents),
            },
        )
        results = body.get("results") or body.get("data")
        if not isinstance(results, list) or not results:
            raise EndpointError(f"no rerank results from {model}: {str(body)[:200]}")
        scored: dict[int, float] = {}
        for r in results:
            item = r if isinstance(r, dict) else {}
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if (
                not isinstance(index, int)
                or not 0 <= index < len(documents)
                or index in scored
                or not isinstance(score, int | float)
            ):
                raise EndpointError(f"malformed rerank result from {model}: {r!r}")
            scored[index] = float(score)
        return sorted(scored.items(), key=lambda t: (-t[1], t[0]))
