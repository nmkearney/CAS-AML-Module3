"""Phase 1 tool signatures (DRAFT, to be agreed on day 1).

These are the seven tools the prototype needs. Each is implemented in its own
module and called here with the active
:class:`~chatcde.tools.context.ToolContext` (config, catalogue, endpoint), so the
signatures stay as agreed. Tools not built yet raise ``NotImplementedError``.

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

    Combines keyword (BM25) and meaning (embedding) search, then reranks, so
    exact names and paraphrases in other languages are both found. Each passage
    is a chunk of an abstract, the full text, or (when a record has neither)
    its title and keywords; ``chunk.source`` says which.

    Args:
        query: Natural-language search text, in any language.
        filters: Optional restrictions (years, units, types, languages,
            countries, regions, sub-areas, methods, SDGs).
        k: Number of passages to return after reranking (capped by the
            `tools.max_k` setting, 20 by default).

    Returns:
        Passages ranked by relevance, best first, each with a ``score`` and a
        ``citation`` (BORIS ID, title, year, DOI, BORIS URL, page). Several
        passages may come from the same publication.
    """
    from chatcde.tools import publications
    from chatcde.tools.context import get_context

    return publications.search_publications(get_context(), query, filters, k)


def get_publication(record_id: str) -> PublicationDetail:
    """Return metadata, authors, tags and download status for one publication.

    Use it to see the abstract, authors and CDE units of a search hit, its
    controlled-vocabulary tags with confidence and evidence, and whether its
    full text is available (``record.has_fulltext``, ``record.access``,
    ``download.status``).

    Args:
        record_id: BORIS item UUID, a DOI (publisher's or BORIS's own, with or
            without ``https://doi.org/``) or a BORIS handle.
    """
    from chatcde.tools import publications
    from chatcde.tools.context import get_context

    return publications.get_publication(get_context(), record_id)


def read_section(
    record_id: str, page_start: int, page_end: int | None = None
) -> list[Chunk]:
    """Return the text of a page range of one publication, for quoting.

    Gives the full-text passages that overlap the pages, verbatim and in
    reading order, at most `tools.max_section_pages` (10 by default) pages per
    call. Neighbouring passages share a few sentences. Only records with parsed
    full text have pages; for the others, use the abstract from
    ``get_publication``.

    Args:
        record_id: BORIS item UUID (a DOI or handle also works).
        page_start: First page (1-based, as in the PDF).
        page_end: Last page; defaults to ``page_start``.
    """
    from chatcde.tools import publications
    from chatcde.tools.context import get_context

    return publications.read_section(get_context(), record_id, page_start, page_end)


def list_vocabulary(dimension: Dimension) -> list[VocabTerm]:
    """List the controlled-vocabulary terms of one dimension with record counts.

    Use it to learn the exact codes to pass in ``Filters`` and to see at a
    glance which terms are rare. Terms come in vocabulary order; codes found in
    the tags but not in the vocabulary (``other``, countries, UN M49 regions)
    follow. ``record_count`` is the number of records with a tag of that term
    at confidence 0.5 or more, over the whole catalogue.

    Args:
        dimension: One of the 18 schema dimensions.
    """
    from chatcde.tools import counts
    from chatcde.tools.context import get_context

    return counts.list_vocabulary(get_context(), dimension)


def count_by(
    row: Dimension,
    col: Dimension | None = None,
    filters: Filters | None = None,
) -> CountMatrix:
    """Count publications by one or two dimensions.

    Counts records, not tags: a record with two methods counts under both.
    Only tags with confidence at least ``filters.min_confidence`` count. With
    two dimensions, cells and totals count only records tagged on both;
    combinations that never occur are left out (their count is 0). Every cell
    lists the BORIS IDs behind it. All publication types count; use
    ``find_gaps`` for gap analysis.

    Args:
        row: Dimension for the rows, e.g. ``sub_area``.
        col: Optional dimension for the columns, e.g. ``method``.
        filters: Optional restrictions.

    Returns:
        A count matrix with row and column totals, the total number of matching
        records, and the number of matching records without tags (on the row
        dimension, or on either dimension when there are two).
    """
    from chatcde.tools import counts
    from chatcde.tools.context import get_context

    return counts.count_by(get_context(), row, col, filters)


def find_gaps(
    row: Dimension,
    col: Dimension,
    filters: Filters | None = None,
    max_ratio: float = 0.25,
) -> list[GapCell]:
    """Find combinations of two dimensions that CDE has rarely covered.

    A cell is a gap when its count is at most ``max_ratio`` times the count
    expected if the two dimensions were independent: ``expected = row total x
    column total / records tagged on both``. Cells expected to hold fewer than
    5 records (``gaps.min_expected``) are not judged. Conference items are left
    out unless ``filters.pub_types`` names the types to use. Before calling a
    gap real, check ``coverage_report`` for the same filters and read a few
    records with ``search_publications``.

    Args:
        row: First dimension, e.g. ``sub_area``.
        col: Second dimension, e.g. ``method``.
        filters: Optional restrictions.
        max_ratio: Threshold on count / expected.

    Returns:
        Gap cells, lowest ratio first, each with its count, expected count,
        ratio and the BORIS IDs behind the count.
    """
    from chatcde.tools import counts
    from chatcde.tools.context import get_context

    return counts.find_gaps(get_context(), row, col, filters, max_ratio)


def coverage_report(filters: Filters | None = None) -> CoverageReport:
    """Report how complete the data is for a set of filters.

    Use this before claiming that something is rare or missing: a topic can look
    rare only because few records of that kind have readable text.

    Each matching record is counted once, as ``with_fulltext`` (parsed text),
    ``abstract_only`` or ``metadata_only``; the three add up to
    ``total_records``. ``by_year``, ``by_type``, ``by_language`` (first listed
    language) and ``by_access`` count the same records. ``low_confidence_tags``
    is the number of tags below ``filters.min_confidence`` on these records.
    ``edition`` says whether restricted full texts are included (full) or not
    (open).

    Args:
        filters: Optional restrictions (years, units, types, languages, tags).

    Returns:
        Counts with their denominator, ``total_records``.
    """
    # Implemented by Track A (issue #11); the logic is in chatcde.tools.coverage.
    from chatcde import catalogue
    from chatcde.config import load_config
    from chatcde.tools import coverage

    cfg = load_config()
    if not cfg.data.catalogue.is_file():
        raise FileNotFoundError(f"no catalogue at {cfg.data.catalogue}")
    con = catalogue.connect(cfg.data.catalogue, read_only=True)
    try:
        return coverage.build_report(con, filters, coverage.local_edition(cfg.data.dir))
    finally:
        con.close()
