"""Transcribe page images with the local vision model, and cache the answers.

Pages without a usable text layer (decision 0014) are rendered to PNG
(:func:`chatcde.parse.pdf.render_png`) and sent to the model in the ``vision``
role, through the OpenAI-compatible ``/chat/completions`` endpoint, with the
versioned prompt ``<prompts.dir>/<parse.ocr_prompt>.md``. The client is Track B's
:class:`~chatcde.store.endpoint.EndpointClient` (TLS via certifi, retries on
timeouts, 429 and 5xx), used as it is.

Vision calls are the slow and costly part of parsing, so every answer is kept in
``<data.text_dir>/ocr/<record_id>.jsonl``, keyed by the page image's SHA-256,
the model and the prompt version. ``chatcde parse --force`` re-reads every PDF
but reuses these answers; a new model or prompt version asks again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chatcde.harvest.common import now
from chatcde.store.endpoint import EndpointClient, EndpointError

#: What the prompt asks the model to answer for a page without text.
NO_TEXT = "[NO TEXT]"


class OcrError(Exception):
    """The vision model gave no usable transcription for a page."""


class EndpointDown(OcrError):
    """The request itself failed (after retries): the endpoint, not the page.

    Only these count towards ``parse.max_consecutive_failures``; a page the model
    cannot read (a loop, a rejected script) says nothing about the endpoint.
    """


def load_prompt(prompts_dir: Path, name: str) -> str:
    """Read the prompt ``<prompts_dir>/<name>.md`` (e.g. ``ocr_page.v1``).

    Raises:
        FileNotFoundError: The prompt file does not exist.
    """
    return (prompts_dir / f"{name}.md").read_text(encoding="utf-8").strip()


def png_sha256(png: bytes) -> str:
    """Hex SHA-256 of a rendered page, the cache key for its transcription."""
    return hashlib.sha256(png).hexdigest()


def _strip_fences(text: str) -> str:
    """Remove a Markdown code fence around the whole answer, if the model added one."""
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])
    return text.strip()


def main_script(text: str) -> str | None:
    """The Unicode script most letters of ``text`` belong to, e.g. ``LATIN``, ``THAI``.

    The script is the first word of each letter's Unicode name, which is close
    enough to the script for the scripts in this corpus. ``None`` if ``text`` has
    no letters.
    """
    counts = Counter(
        unicodedata.name(c, "UNKNOWN").split()[0] for c in text if c.isalpha()
    )
    return counts.most_common(1)[0][0] if counts else None


def check_answer(text: str, reject_scripts: Sequence[str]) -> None:
    """Refuse a transcription written mostly in a script listed as a known misreading.

    qwen3-vl writes Lao pages as plausible Thai (decision 0014), and no record in
    the corpus contains Thai, so Thai output means the page was misread. Used on
    new answers and on cached ones, so a rejected answer never comes back.

    Raises:
        OcrError: The answer's main script is in ``reject_scripts``.
    """
    script = main_script(text)
    if script is not None and script in reject_scripts:
        raise OcrError(
            f"answer in {script} script, rejected (parse.ocr_reject_scripts)"
        )


def ocr_page(
    client: EndpointClient,
    *,
    model: str,
    prompt: str,
    png: bytes,
    max_tokens: int,
    retry_repetition_penalty: float | None = None,
    reject_scripts: Sequence[str] = (),
) -> str:
    """Ask the vision model to transcribe one page image.

    Args:
        client: Endpoint client (shared between threads).
        model: Model name, from ``models.vision``.
        prompt: The OCR prompt text.
        png: The rendered page.
        max_tokens: Answer budget; an answer cut off at this limit is refused,
            because a truncated page would pass for a complete one. Cut-offs are
            almost always loops (one phrase repeated), not long pages.
        retry_repetition_penalty: If set, a cut-off answer is asked for once more
            with this vLLM ``repetition_penalty``, which breaks most loops. It is
            not used on the first attempt, so normal pages are not affected.
        reject_scripts: Unicode scripts whose answers are refused
            (:func:`check_answer`).

    Returns:
        The transcription, or ``""`` if the model found no text on the page.

    Raises:
        OcrError: The request failed, the answer was cut off (also after the
            retry), it was empty, or it was in a rejected script.
    """
    text = _ask(client, model, prompt, png, max_tokens, None)
    if text is None and retry_repetition_penalty is not None:
        text = _ask(client, model, prompt, png, max_tokens, retry_repetition_penalty)
    if text is None:
        raise OcrError(f"answer cut off at max_tokens={max_tokens}")
    check_answer(text, reject_scripts)
    return text


def _ask(
    client: EndpointClient,
    model: str,
    prompt: str,
    png: bytes,
    max_tokens: int,
    repetition_penalty: float | None,
) -> str | None:
    """One transcription request; ``None`` if the answer hit ``max_tokens``.

    Raises:
        OcrError: The request failed, or the answer was missing or empty.
    """
    image = base64.b64encode(png).decode("ascii")
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image}"},
                    },
                ],
            }
        ],
    }
    if repetition_penalty is not None:
        payload["repetition_penalty"] = repetition_penalty  # vLLM sampling option
    try:
        body = client.post_json("/chat/completions", payload)
    except EndpointError as exc:
        raise EndpointDown(str(exc)) from exc
    choices = body.get("choices") or []
    if not choices:
        raise OcrError(f"no choices in the reply from {model}")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        return None
    text = _strip_fences((choice.get("message") or {}).get("content") or "")
    if not text:
        raise OcrError(f"empty answer from {model}")
    return "" if text == NO_TEXT else text


@dataclass(frozen=True)
class CacheKey:
    """What a cached transcription depends on."""

    page: int
    png_sha256: str
    model: str
    prompt: str


class OcrCache:
    """Transcriptions of one record's pages, in ``<dir>/<record_id>.jsonl``.

    One JSON line per answer; later lines win. Only the main thread writes.
    """

    def __init__(self, cache_dir: Path, record_id: str) -> None:
        """Load the cached answers of ``record_id`` (none if the file is missing)."""
        self.path = cache_dir / f"{record_id}.jsonl"
        self._items: dict[CacheKey, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    key = CacheKey(d["page"], d["png_sha256"], d["model"], d["prompt"])
                    self._items[key] = d["text"]
                except (ValueError, KeyError, TypeError):
                    continue  # a line cut short by a crash; it is asked again

    def __len__(self) -> int:
        """Number of cached transcriptions."""
        return len(self._items)

    def get(self, key: CacheKey) -> str | None:
        """The cached transcription for ``key``, or ``None``."""
        return self._items.get(key)

    def put(self, key: CacheKey, text: str) -> None:
        """Store a transcription and append it to the cache file at once."""
        self._items[key] = text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "page": key.page,
            "png_sha256": key.png_sha256,
            "model": key.model,
            "prompt": key.prompt,
            "text": text,
            "at": now().isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
