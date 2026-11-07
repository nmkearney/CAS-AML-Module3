"""Split a record's text into searchable passages (:class:`~chatcde.schemas.Chunk`).

Pure functions, no I/O: :mod:`chatcde.store.index` reads the catalogue, calls
:func:`chunk_record` per record and writes the result. Design and parameters are
recorded in docs/decisions/0011-chunking.md. In short:

* Text is cleaned (Unicode NFKC, soft hyphens removed, hyphenated line breaks
  rejoined, whitespace collapsed) and split into *units*: sentences within
  paragraphs, each remembering its page.
* Units are packed greedily into chunks of at most ``chunk_tokens`` estimated
  tokens; each chunk after the first starts with the trailing units of the
  previous one, up to ``overlap_tokens``, so a sentence near a boundary is found
  with its context.
* Tokens are *estimated* (word-like pieces × ``tokens_per_word``) so chunking
  needs no tokenizer download; the factor is set conservatively so chunks stay
  within the reranker's 1K-token context.
* A record gets abstract chunks if it has an abstract, full-text chunks if it has
  pages, and one metadata chunk (title, keywords, container) if it has neither,
  so every record can be found by search.

Chunk text is never prefixed with the title, so ``read_section`` can quote it
verbatim.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

from chatcde.schemas import Chunk, PageText, Record, TextSource

#: Bump when the chunking logic changes, so ``chatcde index chunk`` redoes every
#: record (the version is part of each record's fingerprint).
CHUNKER_VERSION = "1"

#: Word-like pieces counted for the token estimate: runs of letters/digits, or
#: single punctuation marks. Joining units with spaces never changes the count.
_PIECE_RE = re.compile(r"\w+|[^\w\s]")

#: Hyphen at a line end followed by a lower-case letter: "Entwick-\nlung".
_HYPHEN_BREAK_RE = re.compile(r"(\w)-[ \t]*\n[ \t]*(?=[^\W\d_])")

#: Blank line(s) between paragraphs.
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")

#: Sentence end: ., ! , ? or … then space, then an (optionally quoted or
#: bracketed) capital letter or digit. Abbreviations are split occasionally;
#: packing makes that harmless.
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[\"'„“«(\[]?[A-ZÀ-ÖØ-Þ0-9])")


class ChunkSettings(BaseModel):
    """Chunk size settings, from the ``retrieval`` section of the config."""

    chunk_tokens: int = Field(gt=0, description="Maximum estimated tokens per chunk")
    overlap_tokens: int = Field(
        ge=0, description="Estimated tokens repeated from the previous chunk"
    )
    tokens_per_word: float = Field(
        gt=0, description="Estimated tokens per word-like piece"
    )

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self) -> ChunkSettings:
        """An overlap as large as a chunk would never make progress."""
        if self.overlap_tokens >= self.chunk_tokens:
            raise ValueError("overlap_tokens must be smaller than chunk_tokens")
        return self

    @property
    def max_pieces(self) -> int:
        """Most word-like pieces a chunk may hold within ``chunk_tokens``."""
        return max(1, math.floor(self.chunk_tokens / self.tokens_per_word))

    @property
    def overlap_pieces(self) -> int:
        """Most word-like pieces carried over into the next chunk."""
        return math.floor(self.overlap_tokens / self.tokens_per_word)


@dataclass(frozen=True)
class Unit:
    """A sentence (or a piece of an over-long sentence) with where it came from.

    Attributes:
        text: Cleaned text, without line breaks.
        page: Page number, or ``None`` for abstracts and metadata.
        paragraph: Running paragraph number, to keep paragraph breaks in chunks.
        pieces: Number of word-like pieces (see :func:`count_pieces`).
        language: Language of the page or record, if known.
    """

    text: str
    page: int | None
    paragraph: int
    pieces: int
    language: str | None = None


def count_pieces(text: str) -> int:
    """Count word-like pieces: runs of letters/digits and single punctuation marks."""
    return len(_PIECE_RE.findall(text))


def estimate_tokens(text: str, tokens_per_word: float) -> int:
    """Estimate the number of model tokens in ``text`` without a tokenizer.

    Args:
        text: Any text.
        tokens_per_word: Tokens per word-like piece (``retrieval.tokens_per_word``).

    Returns:
        ``ceil(pieces × tokens_per_word)``; 0 for empty text.
    """
    return math.ceil(count_pieces(text) * tokens_per_word)


def clean_text(text: str) -> str:
    """Normalise extracted PDF text while keeping paragraph breaks.

    Applies Unicode NFKC (ligatures, full-width forms), removes soft hyphens,
    rejoins words hyphenated across a line break ("Entwick-" + line break +
    "lung" → "Entwicklung"; before an upper-case letter the hyphen is kept:
    "Nord-Süd"), joins the remaining lines of a paragraph with spaces and
    collapses runs of spaces. Paragraphs stay separated by one blank line.
    """
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK_RE.sub(_rejoin_hyphen, text)
    paragraphs = (" ".join(p.split()) for p in _PARAGRAPH_RE.split(text))
    return "\n\n".join(p for p in paragraphs if p)


def _rejoin_hyphen(match: re.Match[str]) -> str:
    """Drop the line break; drop the hyphen too if the next letter is lower case."""
    nxt = match.string[match.end() : match.end() + 1]
    return match.group(1) if nxt.islower() else match.group(1) + "-"


def split_units(
    text: str,
    *,
    page: int | None = None,
    first_paragraph: int = 0,
    language: str | None = None,
) -> list[Unit]:
    """Clean ``text`` and split it into sentence units.

    Args:
        text: Raw page, abstract or metadata text.
        page: Page number the text comes from, if any.
        first_paragraph: Paragraph number to start counting from, so numbers
            stay unique across the pages of a record.
        language: Language recorded on each unit.

    Returns:
        Units in reading order; empty for blank text.
    """
    units = []
    for i, para in enumerate(clean_text(text).split("\n\n")):
        for sentence in _SENTENCE_RE.split(para):
            sentence = sentence.strip()
            if sentence:
                units.append(
                    Unit(
                        sentence,
                        page,
                        first_paragraph + i,
                        count_pieces(sentence),
                        language,
                    )
                )
    return units


def _split_long(unit: Unit, max_pieces: int) -> list[Unit]:
    """Split a unit longer than ``max_pieces`` on spaces into parts that fit.

    A single space-free token longer than the limit (e.g. a long URL) is kept
    whole: it cannot be split meaningfully, and it is rare.
    """
    if unit.pieces <= max_pieces:
        return [unit]
    parts: list[Unit] = []
    words: list[str] = []
    n = 0
    for word in unit.text.split(" "):
        w = count_pieces(word)
        if words and n + w > max_pieces:
            text = " ".join(words)
            parts.append(Unit(text, unit.page, unit.paragraph, n, unit.language))
            words, n = [], 0
        words.append(word)
        n += w
    if words:
        text = " ".join(words)
        parts.append(Unit(text, unit.page, unit.paragraph, n, unit.language))
    return parts


def pack_units(units: Iterable[Unit], settings: ChunkSettings) -> list[list[Unit]]:
    """Group units into chunks that fit ``chunk_tokens``, with overlap.

    Greedy: units are added until the next one would not fit. The next chunk
    then starts with the trailing units of the previous chunk worth at most
    ``overlap_tokens`` (never the whole previous chunk, so every chunk adds new
    text).

    Args:
        units: Units in reading order.
        settings: Chunk size settings.

    Returns:
        Lists of units, one list per chunk.
    """
    limit = settings.max_pieces
    groups: list[list[Unit]] = []
    current: list[Unit] = []
    size = 0
    for big in units:
        for unit in _split_long(big, limit):
            if current and size + unit.pieces > limit:
                groups.append(current)
                current = _overlap_tail(current, settings.overlap_pieces)
                while current and sum(u.pieces for u in current) + unit.pieces > limit:
                    current.pop(0)
                size = sum(u.pieces for u in current)
            current.append(unit)
            size += unit.pieces
    if current:
        groups.append(current)
    return groups


def _overlap_tail(group: Sequence[Unit], max_pieces: int) -> list[Unit]:
    """Trailing units of ``group`` worth at most ``max_pieces``, never all of it."""
    tail: list[Unit] = []
    n = 0
    for unit in reversed(group[1:]):
        if n + unit.pieces > max_pieces:
            break
        tail.insert(0, unit)
        n += unit.pieces
    return tail


def join_units(units: Sequence[Unit]) -> str:
    """Chunk text: units joined by spaces, with a line break between paragraphs."""
    out: list[str] = []
    for i, unit in enumerate(units):
        if i:
            out.append("\n" if unit.paragraph != units[i - 1].paragraph else " ")
        out.append(unit.text)
    return "".join(out)


def metadata_text(record: Record) -> str:
    """Text for a record without abstract or pages: title, keywords, container."""
    lines = [record.title]
    if record.keywords:
        lines.append("Keywords: " + "; ".join(record.keywords))
    if record.container:
        lines.append("In: " + record.container)
    return "\n\n".join(lines)


def chunk_record(
    record: Record, pages: Sequence[PageText], settings: ChunkSettings
) -> list[Chunk]:
    """Build all chunks of one record.

    Order and IDs: abstract chunks first, then full-text chunks, numbered
    ``{record_id}:0``, ``{record_id}:1``, ...; a record with neither abstract
    nor pages gets a single metadata chunk ``{record_id}:0``.

    Args:
        record: The catalogue record (title, abstract, languages, ...).
        pages: Its parsed pages, in any order; blank pages are ignored.
        settings: Chunk size settings.

    Returns:
        At least one chunk; every chunk's ``token_count`` is at most
        ``settings.chunk_tokens`` unless a single space-free token is longer.
    """
    record_lang = record.languages[0] if record.languages else None
    sources: list[tuple[TextSource, list[Unit]]] = []

    if record.abstract and record.abstract.strip():
        source = record.abstract_source or TextSource.ABSTRACT_BORIS
        sources.append((source, split_units(record.abstract, language=record_lang)))

    page_units: list[Unit] = []
    for p in sorted(pages, key=lambda p: p.page):
        first = page_units[-1].paragraph + 1 if page_units else 0
        lang = p.language or record_lang
        page_units += split_units(
            p.text, page=p.page, first_paragraph=first, language=lang
        )
    if page_units:
        sources.append((TextSource.FULLTEXT, page_units))

    if not sources:
        units = split_units(metadata_text(record), language=record_lang)
        sources.append((TextSource.METADATA, units))

    chunks: list[Chunk] = []
    for source, units in sources:
        for group in pack_units(units, settings):
            text = join_units(group)
            page_nums = [u.page for u in group if u.page is not None]
            chunks.append(
                Chunk(
                    chunk_id=f"{record.record_id}:{len(chunks)}",
                    record_id=record.record_id,
                    text=text,
                    page_start=min(page_nums) if page_nums else None,
                    page_end=max(page_nums) if page_nums else None,
                    section=None,
                    language=group[0].language,
                    token_count=estimate_tokens(text, settings.tokens_per_word),
                    source=source,
                )
            )
    return chunks
