"""Read a PDF's text layer page by page and decide which pages need OCR.

PyMuPDF (``page.get_text("text")``) is the parser (decision 0014): it is fast,
needs no ML models, and is the extractor the test fixtures were made with, so
fixture pages and parsed pages match. Text is kept exactly as extracted;
cleaning (hyphenation, whitespace) is the chunker's job (decision 0011).

Every page is put in one :class:`PageClass`:

* ``text``: the text layer is used as it is.
* ``ocr``: almost no text, but the page is covered by images (a scan) or by
  many vector paths (outlined text). It is sent to the vision model.
* ``garbled``: the text layer looks broken (replacement characters,
  private-use glyphs, ``(cid:NN)`` codes, or mostly symbols). Also OCR.
* ``blank``: almost no text and nothing to read. Stored with its little text,
  no model call.

The thresholds come from the ``parse`` section of the config.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pymupdf

from chatcde.config import ParseConfig

#: Bump when extraction or classification changes, so ``chatcde parse`` redoes
#: every record (the version is stored per record in ``parse_state``).
PARSER_VERSION = "1"

# MuPDF reports many harmless repairs (broken xref tables, missing layer
# configs) on stderr; keep them out of the console. They are collected with
# :func:`take_warnings` and logged at DEBUG level instead.
pymupdf.TOOLS.mupdf_display_errors(False)

#: ``(cid:12)``: unmapped glyph codes, as some extractors write them.
_CID_RE = re.compile(r"\(cid:\d+\)")


class PageClass(StrEnum):
    """What the parse stage does with a page (see the module docstring)."""

    TEXT = "text"
    OCR = "ocr"
    GARBLED = "garbled"
    BLANK = "blank"

    @property
    def needs_ocr(self) -> bool:
        """True for the classes sent to the vision model."""
        return self in (PageClass.OCR, PageClass.GARBLED)


@dataclass(frozen=True)
class RawPage:
    """What the text layer of one page holds, before any OCR.

    Attributes:
        page: 1-based page number.
        text: Text exactly as PyMuPDF extracted it.
        image_coverage: Share of the page area covered by images (0–1; overlaps
            are counted twice and the sum is capped at 1).
        drawings: Number of vector paths; only counted on low-text pages,
            because it is slow on detailed maps (otherwise 0).
    """

    page: int
    text: str
    image_coverage: float
    drawings: int


def parser_name() -> str:
    """Parser and version recorded per run, e.g. ``pymupdf 2.1 (MuPDF 1.28.6)``."""
    return f"pymupdf {pymupdf.version[0]} (MuPDF {pymupdf.version[1]})"


def non_space(text: str) -> int:
    """Number of characters in ``text`` that are not whitespace."""
    return sum(1 for c in text if not c.isspace())


def _is_garbage_char(c: str) -> bool:
    """Replacement character, private-use glyph or stray control character."""
    o = ord(c)
    return o == 0xFFFD or 0xE000 <= o <= 0xF8FF or (o < 32 and c not in "\n\r\t")


def garbage_ratio(text: str) -> float:
    """Share of non-space characters that are garbage or part of ``(cid:NN)``.

    Returns 0.0 for text without non-space characters.
    """
    n = non_space(text)
    if n == 0:
        return 0.0
    cid = sum(len(m) for m in _CID_RE.findall(text))
    bad = sum(1 for c in _CID_RE.sub("", text) if _is_garbage_char(c))
    return min(1.0, (bad + cid) / n)


def alnum_ratio(text: str) -> float:
    """Share of word characters among characters that are not space or punctuation.

    Word characters are letters, digits and combining marks (Unicode category
    M, needed for scripts such as Burmese or Devanagari, whose vowel signs are
    marks). Punctuation (category P: dot leaders in forms and contents pages,
    dashes, brackets) is left out, so ``Methods: ........`` counts as text. What
    is left is symbols and control characters, which is what a broken font
    encoding produces.

    Returns 1.0 when nothing is left, so empty pages are never called garbled.
    """
    chars = [c for c in text if not c.isspace() and not _is_punct(c)]
    if not chars:
        return 1.0
    return sum(1 for c in chars if c.isalnum() or _is_mark(c)) / len(chars)


def _is_mark(c: str) -> bool:
    """True for combining marks (categories Mn, Mc, Me)."""
    return unicodedata.category(c).startswith("M")


def _is_punct(c: str) -> bool:
    """True for Unicode punctuation (categories Pc, Pd, Ps, Pe, Pi, Pf, Po)."""
    return unicodedata.category(c).startswith("P")


def classify(raw: RawPage, settings: ParseConfig) -> PageClass:
    """Decide what to do with one page (see the module docstring)."""
    if non_space(raw.text) < settings.min_chars:
        if (
            raw.image_coverage >= settings.min_image_coverage
            or raw.drawings >= settings.min_drawings
        ):
            return PageClass.OCR
        return PageClass.BLANK
    if (
        garbage_ratio(raw.text) > settings.max_garbage_ratio
        or alnum_ratio(raw.text) < settings.min_alnum_ratio
    ):
        return PageClass.GARBLED
    return PageClass.TEXT


def _image_coverage(page: pymupdf.Page) -> float:
    """Share of the page covered by images, clipped to the page (0–1)."""
    area = page.rect.width * page.rect.height
    if area <= 0:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        box = pymupdf.Rect(info["bbox"]) & page.rect
        if not box.is_empty:
            covered += box.width * box.height
    return min(1.0, covered / area)


def extract_pages(doc: pymupdf.Document, settings: ParseConfig) -> list[RawPage]:
    """Read every page's text layer, with what is needed to classify it.

    Args:
        doc: An open PDF.
        settings: The ``parse`` config (``min_chars`` decides where vector
            paths are counted).

    Returns:
        One :class:`RawPage` per page, in page order.
    """
    pages = []
    for i, page in enumerate(doc, 1):
        text = page.get_text("text")
        low = non_space(text) < settings.min_chars
        pages.append(
            RawPage(
                page=i,
                text=text,
                image_coverage=_image_coverage(page) if low else 0.0,
                drawings=len(page.get_cdrawings()) if low else 0,
            )
        )
    return pages


def open_pdf(path: Path) -> pymupdf.Document:
    """Open a PDF, refusing encrypted files and non-PDFs.

    Raises:
        ValueError: The file is encrypted or not a PDF.
        pymupdf.FileDataError: The file cannot be read at all.
    """
    doc = pymupdf.open(path)
    if not doc.is_pdf:
        doc.close()
        raise ValueError(f"{path.name} is not a PDF")
    if doc.needs_pass:
        doc.close()
        raise ValueError(f"{path.name} is encrypted")
    return doc


def render_png(page: pymupdf.Page, *, dpi: int, max_px: int) -> bytes:
    """Render a page as PNG for the vision model.

    The resolution is ``dpi``, lowered so the longer side stays within
    ``max_px`` (large-format posters and maps would otherwise be huge).
    """
    long_side_pt = max(page.rect.width, page.rect.height) or 1.0
    zoom = min(dpi / 72.0, max_px / long_side_pt)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def take_warnings() -> str:
    """Return and clear the warnings MuPDF collected since the last call."""
    return pymupdf.TOOLS.mupdf_warnings(reset=True)
