"""Detect the language of each page, offline (decision 0014).

BORIS language codes are per record and partly wrong: ``me`` (49 records) covers
English, German, Spanish and French titles, and multilingual reports mix
editions. So each page gets its own language, detected with lingua
(``lingua-language-detector``), restricted to the candidates in
``parse.languages`` so short German pages are not taken for Dutch or Afrikaans.

A page gets ``None`` when it is too short or the detector is not confident; the
chunker then falls back to the record's BORIS language.
"""

from __future__ import annotations

from functools import lru_cache

from lingua import IsoCode639_1, LanguageDetector, LanguageDetectorBuilder

from chatcde.config import ParseConfig
from chatcde.parse.pdf import non_space


@lru_cache(maxsize=4)
def _detector(languages: tuple[str, ...]) -> LanguageDetector:
    """Build (once per language set) a detector for the given ISO 639-1 codes.

    lingua needs at least two candidates; the config enforces that.

    Raises:
        ValueError: A code is not an ISO 639-1 code lingua knows.
    """
    try:
        codes = [getattr(IsoCode639_1, code.upper()) for code in languages]
    except AttributeError as exc:
        raise ValueError(f"unknown ISO 639-1 code in {languages}") from exc
    return LanguageDetectorBuilder.from_iso_codes_639_1(*codes).build()


def detect_language(text: str, settings: ParseConfig) -> str | None:
    """ISO 639-1 code of the main language of ``text``, or ``None``.

    Args:
        text: Page text (text layer or OCR).
        settings: The ``parse`` config: candidate ``languages``,
            ``min_chars_language``, ``min_language_confidence`` and
            ``language_sample_chars``.

    Returns:
        A lower-case code from ``settings.languages``, or ``None`` if the text
        has fewer than ``min_chars_language`` non-space characters or the best
        language's confidence is below ``min_language_confidence``.
    """
    if non_space(text) < settings.min_chars_language:
        return None
    sample = text[: settings.language_sample_chars]
    values = _detector(tuple(settings.languages)).compute_language_confidence_values(
        sample
    )
    if not values or values[0].value < settings.min_language_confidence:
        return None
    code = values[0].language.iso_code_639_1.name.lower()
    return code if code in settings.languages else None
