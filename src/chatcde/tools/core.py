"""Phase 1 tool signatures (DRAFT, to be agreed on day 1).

These are the seven tools the prototype needs. Track B implements them; until
then they raise ``NotImplementedError`` so the CLI and agent can be wired up
against the signatures.

Rules for every tool (see CLAUDE.md):

* Plain, typed Python function; no CLI or LLM code inside.
* The docstring is shown to the LLM as the tool description, so it must say
  plainly what the tool does, what each argument means and what it returns.
* Return pydantic models from :mod:`chatcde.schemas`, never free text.
* Counts include their denominators; results carry BORIS IDs for citation.
"""

from __future__ import annotations

from chatcde.schemas import (
    Chunk,
    CountMatrix,
    CoverageReport,
    Dimension,
    Filters,
    GapCell,
    Passage,
    PublicationDetail,
    VocabTerm,
)


def search_publications(
    query: str, filters: Filters | None = None, k: int = 8
) -> list[Passage]:
    """Search CDE publications for passages relevant to a question.

    Args:
        query: Natural-language search text, in any language.
        filters: Optional restrictions (years, units, regions, ...).
        k: Number of passages to return after reranking.

    Returns:
        Passages ranked by relevance, each with a citation.
    """
    raise NotImplementedError


def get_publication(record_id: str) -> PublicationDetail:
    """Return metadata, authors, tags and download status for one publication.

    Args:
        record_id: BORIS item UUID (or a DOI, which is resolved to a record).
    """
    raise NotImplementedError


def read_section(
    record_id: str, page_start: int, page_end: int | None = None
) -> list[Chunk]:
    """Return the text of a page range of one publication, for quoting.

    Args:
        record_id: BORIS item UUID.
        page_start: First page (1-based).
        page_end: Last page; defaults to ``page_start``.
    """
    raise NotImplementedError


def list_vocabulary(dimension: Dimension) -> list[VocabTerm]:
    """List the controlled-vocabulary terms of one dimension with record counts.

    Args:
        dimension: One of the 17 schema dimensions.
    """
    raise NotImplementedError


def count_by(
    row: Dimension,
    col: Dimension | None = None,
    filters: Filters | None = None,
) -> CountMatrix:
    """Count publications by one or two dimensions.

    Args:
        row: Dimension for the rows, e.g. ``sub_area``.
        col: Optional dimension for the columns, e.g. ``method``.
        filters: Optional restrictions.

    Returns:
        A count matrix with row and column totals, the total number of matching
        records, and the number of matching records without tags.
    """
    raise NotImplementedError


def find_gaps(
    row: Dimension,
    col: Dimension,
    filters: Filters | None = None,
    max_ratio: float = 0.25,
) -> list[GapCell]:
    """Find combinations of two dimensions that CDE has rarely covered.

    A cell is a gap when its count is at most ``max_ratio`` times the count
    expected if the two dimensions were independent.

    Args:
        row: First dimension, e.g. ``sub_area``.
        col: Second dimension, e.g. ``method``.
        filters: Optional restrictions.
        max_ratio: Threshold on count / expected.
    """
    raise NotImplementedError


def coverage_report(filters: Filters | None = None) -> CoverageReport:
    """Report how complete the data is for a set of filters.

    Use this before claiming that something is rare or missing.

    Args:
        filters: Optional restrictions.
    """
    raise NotImplementedError
