"""Counting tools: ``list_vocabulary``, ``count_by`` and ``find_gaps`` (issue #24).

Every number here comes from SQL over the catalogue, never from the LLM, and
carries its denominator (CLAUDE.md). See docs/decisions/0021-counts-and-gaps.md.

Counting rules:

* A **record** is counted, not a tag: a record with three methods counts once
  under each, so row totals can add up to more than the number of records.
* Only tags with ``confidence >= filters.min_confidence`` count (0.5 by default).
* ``total_records`` is the number of records matching the filters, tagged or not.
* **One dimension:** ``untagged_records`` is the number of matching records with
  no tag on it.
* **Two dimensions:** cells, row totals and column totals count only the records
  tagged on *both*; ``untagged_records`` is the rest of the matching records
  (``total_records - untagged_records`` is the base of the matrix). Pairs that
  never occur are not listed as cells: their count is 0.

Gaps compare each cell with the count expected if the two dimensions were
independent, ``row_total * col_total / base``; cells with a small expected count
(``gaps.min_expected``) are left out because "0 of an expected 0.4" says nothing.
Gap analysis leaves out ``gaps.exclude_types`` (conference items, decision 0018)
unless the filters name publication types.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import duckdb

from chatcde.schemas import (
    CountCell,
    CountMatrix,
    Dimension,
    Filters,
    GapCell,
    VocabTerm,
)
from chatcde.store.filters import FilterError, filter_record_ids
from chatcde.tools.context import ToolContext, ToolError
from chatcde.tools.vocabulary import (
    VocabularyError,
    dimension_entries,
    load_vocabulary,
)

log = logging.getLogger(__name__)


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """True if the catalogue has a table called ``name``."""
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    row = con.execute(sql, [name]).fetchone()
    return bool(row and row[0])


def _base_ids(con: duckdb.DuckDBPyConnection, filters: Filters) -> list[str]:
    """IDs of the records matching ``filters`` (all records if none).

    Raises:
        ToolError: If there are no records, or tag filters but no tags.
    """
    if not _has_table(con, "records"):
        raise ToolError("the catalogue has no records; run `chatcde fetch-data`")
    try:
        ids = filter_record_ids(con, filters)
    except FilterError as exc:
        raise ToolError(str(exc)) from exc
    if ids is None:
        ids = [r for (r,) in con.execute("SELECT record_id FROM records").fetchall()]
    return ids


def _require_tags(con: duckdb.DuckDBPyConnection) -> None:
    """Raise a ToolError if the catalogue has no tags yet."""
    if not _has_table(con, "tags"):
        raise ToolError(
            "the catalogue has no tags yet; run `chatcde tag` or `chatcde fetch-data`"
        )


def _tagged(
    con: duckdb.DuckDBPyConnection,
    dimension: Dimension,
    ids: Sequence[str],
    min_confidence: float,
) -> dict[str, set[str]]:
    """record_id -> set of terms of ``dimension`` (confident tags only)."""
    rows = con.execute(
        "SELECT DISTINCT record_id, term FROM tags WHERE dimension = ? "
        "AND confidence >= ? AND record_id IN (SELECT unnest(?::VARCHAR[]))",
        [dimension.value, min_confidence, list(ids)],
    ).fetchall()
    out: dict[str, set[str]] = {}
    for rid, term in rows:
        out.setdefault(rid, set()).add(term)
    return out


def _sorted_totals(counts: dict[str, int]) -> dict[str, int]:
    """Totals ordered by count (descending), then term."""
    return dict(sorted(counts.items(), key=lambda t: (-t[1], t[0])))


def count_matrix(
    con: duckdb.DuckDBPyConnection,
    row: Dimension,
    col: Dimension | None = None,
    filters: Filters | None = None,
) -> CountMatrix:
    """Count matching records by one or two dimensions (rules in the module doc).

    Cells are ordered by count (descending), then row and column term; their
    ``record_ids`` are sorted.

    Raises:
        ToolError: No records or no tags, or tag filters without tags.
    """
    f = filters or Filters()
    ids = _base_ids(con, f)
    _require_tags(con)
    rows = _tagged(con, row, ids, f.min_confidence)
    cells: dict[tuple[str, str | None], list[str]] = {}
    row_totals: dict[str, int] = {}
    col_totals: dict[str, int] = {}
    if col is None:
        base = rows
        for rid, terms in rows.items():
            for t in terms:
                cells.setdefault((t, None), []).append(rid)
    else:
        cols = _tagged(con, col, ids, f.min_confidence)
        base = {rid: rows[rid] for rid in rows.keys() & cols.keys()}
        for rid in base:
            for c in cols[rid]:
                col_totals[c] = col_totals.get(c, 0) + 1
            for r in rows[rid]:
                for c in cols[rid]:
                    cells.setdefault((r, c), []).append(rid)
    for terms in base.values():
        for t in terms:
            row_totals[t] = row_totals.get(t, 0) + 1
    out_cells = [
        CountCell(row=r, col=c, count=len(rids), record_ids=sorted(rids))
        for (r, c), rids in cells.items()
    ]
    out_cells.sort(key=lambda x: (-x.count, x.row, x.col or ""))
    return CountMatrix(
        row_dimension=row,
        col_dimension=col,
        filters=f,
        cells=out_cells,
        row_totals=_sorted_totals(row_totals),
        col_totals=_sorted_totals(col_totals),
        total_records=len(ids),
        untagged_records=len(ids) - len(base),
    )


@dataclass(frozen=True)
class GapBasis:
    """The filters a gap analysis really used, and what they left out."""

    filters: Filters
    excluded_types: list[str]
    excluded_records: int


def gap_basis(
    con: duckdb.DuckDBPyConnection,
    filters: Filters | None,
    exclude_types: Sequence[str],
) -> GapBasis:
    """Apply ``gaps.exclude_types`` (decision 0018) as an explicit type list.

    If the filters already name publication types, they are kept as they are
    (the user chose). Otherwise ``pub_types`` becomes every type in the catalogue
    except the excluded ones, and the matching records of excluded types are
    counted so the output can say what was left out.
    """
    f = filters or Filters()
    exclude = sorted({t.lower() for t in exclude_types})
    if not exclude or f.pub_types:
        return GapBasis(f, [], 0)
    _base_ids(con, f)  # checks records and filters
    types = [
        t
        for (t,) in con.execute(
            "SELECT DISTINCT lower(pub_type) FROM records ORDER BY 1"
        ).fetchall()
        if t not in exclude
    ]
    excluded = filter_record_ids(con, f.model_copy(update={"pub_types": exclude}))
    return GapBasis(
        f.model_copy(update={"pub_types": types}), exclude, len(excluded or [])
    )


def gap_cells(
    matrix: CountMatrix, *, max_ratio: float, min_expected: float
) -> list[GapCell]:
    """Thin cells of a two-dimensional count matrix.

    Every pair of a row term and a column term seen in the base is considered,
    including pairs that never occur (count 0). A pair is a gap when
    ``expected >= min_expected`` and ``count / expected <= max_ratio``.

    Returns:
        Gaps, most extreme first (lowest ratio, then highest expected count).
    """
    base = matrix.total_records - matrix.untagged_records
    if base == 0:
        return []
    found = {(c.row, c.col): c for c in matrix.cells}
    gaps = []
    for r, r_total in matrix.row_totals.items():
        for c, c_total in matrix.col_totals.items():
            expected = r_total * c_total / base
            if expected < min_expected:
                continue
            cell = found.get((r, c))
            count = cell.count if cell else 0
            ratio = count / expected
            if ratio <= max_ratio:
                gaps.append(
                    GapCell(
                        row=r,
                        col=c,
                        count=count,
                        record_ids=cell.record_ids if cell else [],
                        expected=round(expected, 2),
                        ratio=round(ratio, 3),
                    )
                )
    gaps.sort(key=lambda g: (g.ratio, -g.expected, g.row, g.col or ""))
    return gaps


# ---------------------------------------------------------------------------
# Tools (called from chatcde.tools.core with the active context)
# ---------------------------------------------------------------------------


def list_vocabulary(ctx: ToolContext, dimension: Dimension) -> list[VocabTerm]:
    """Vocabulary terms of ``dimension`` with the records tagged with each.

    Terms come in vocabulary order. Codes that occur in the tags but not in the
    vocabulary (``other``, codes derived from a standard such as countries and
    M49 sub-regions, or terms renamed since tagging) follow, by count; their
    definition says so. ``record_count`` counts records with a tag of at least
    the default ``Filters.min_confidence`` (0.5), over all records and types.

    Raises:
        ToolError: If the vocabulary file is missing or invalid.
    """
    try:
        vocab = load_vocabulary(ctx.cfg.vocab.file)
    except VocabularyError as exc:
        raise ToolError(str(exc)) from exc
    entries = dimension_entries(vocab, dimension)
    counts: dict[str, int] = {}
    with ctx.catalogue() as con:
        if _has_table(con, "tags"):
            counts = dict(
                con.execute(
                    "SELECT term, count(DISTINCT record_id) FROM tags "
                    "WHERE dimension = ? AND confidence >= ? GROUP BY term",
                    [dimension.value, Filters().min_confidence],
                ).fetchall()
            )
    terms = [
        VocabTerm(
            dimension=dimension,
            code=e.code,
            label=e.label,
            definition=e.definition,
            record_count=int(counts.get(e.code, 0)),
        )
        for e in entries
    ]
    known = {e.code for e in entries}
    version = vocab.get("version", ctx.cfg.vocab.version)
    extra = sorted(
        ((code, n) for code, n in counts.items() if code not in known),
        key=lambda t: (-t[1], t[0]),
    )
    for code, n in extra:
        if code == "other":
            label, note = "Other", "Free text in the tag's other_label"
        elif dimension is Dimension.COUNTRY:
            label, note = code, "ISO 3166-1 alpha-3 country code"
        elif dimension is Dimension.REGION:
            label, note = code, "UN M49 sub-region, derived from country tags"
        else:
            label, note = code, f"Used in tags; not a term of vocabulary {version}"
        terms.append(
            VocabTerm(
                dimension=dimension,
                code=code,
                label=label,
                definition=note,
                record_count=int(n),
            )
        )
    return terms


def count_by(
    ctx: ToolContext,
    row: Dimension,
    col: Dimension | None = None,
    filters: Filters | None = None,
) -> CountMatrix:
    """Count records by one or two dimensions; see :func:`count_matrix`."""
    with ctx.catalogue() as con:
        return count_matrix(con, row, col, filters)


@dataclass(frozen=True)
class GapReport:
    """A gap analysis with everything needed to state its denominators."""

    basis: GapBasis
    matrix: CountMatrix
    gaps: list[GapCell]
    min_expected: float


def gap_report(
    ctx: ToolContext,
    row: Dimension,
    col: Dimension,
    filters: Filters | None = None,
    max_ratio: float = 0.25,
    *,
    exclude_types: Sequence[str] | None = None,
) -> GapReport:
    """Run a gap analysis of ``row`` x ``col`` and keep its basis and matrix.

    Args:
        ctx: Tool context.
        row: First dimension.
        col: Second dimension (must differ from ``row``).
        filters: Optional restrictions.
        max_ratio: Largest ``count / expected`` that counts as a gap.
        exclude_types: Publication types to leave out; ``None`` uses
            ``gaps.exclude_types`` from the config, ``[]`` keeps all types.

    Raises:
        ToolError: Same dimension twice, negative ``max_ratio``, no records or tags.
    """
    if row == col:
        raise ToolError("find_gaps needs two different dimensions")
    if max_ratio < 0:
        raise ToolError("max_ratio must be 0 or more")
    cfg = ctx.cfg.gaps
    types = cfg.exclude_types if exclude_types is None else exclude_types
    with ctx.catalogue() as con:
        basis = gap_basis(con, filters, types)
        matrix = count_matrix(con, row, col, basis.filters)
    gaps = gap_cells(matrix, max_ratio=max_ratio, min_expected=cfg.min_expected)
    return GapReport(basis, matrix, gaps, cfg.min_expected)


def find_gaps(
    ctx: ToolContext,
    row: Dimension,
    col: Dimension,
    filters: Filters | None = None,
    max_ratio: float = 0.25,
) -> list[GapCell]:
    """The gap cells of :func:`gap_report`, as the agent's tool returns them."""
    report = gap_report(ctx, row, col, filters, max_ratio)
    gaps, matrix, basis = report.gaps, report.matrix, report.basis
    log.info(
        "find_gaps %s x %s: %d gaps among %d tagged of %d records "
        "(%d records of types %s left out)",
        row.value,
        col.value,
        len(gaps),
        matrix.total_records - matrix.untagged_records,
        matrix.total_records,
        basis.excluded_records,
        ", ".join(basis.excluded_types) or "none",
    )
    return gaps


@dataclass(frozen=True)
class DimensionSummary:
    """How one dimension is used: vocabulary size, terms in use, records tagged."""

    dimension: Dimension
    terms: int
    terms_used: int
    records_tagged: int


def dimension_overview(ctx: ToolContext) -> tuple[list[DimensionSummary], int]:
    """Summarise all 18 dimensions (for ``chatcde schema`` and ``chatcde vocab``).

    Returns:
        One summary per dimension, in schema order, and the number of records in
        the catalogue (the denominator of ``records_tagged``).
    """
    out = []
    for dim in Dimension:
        terms = list_vocabulary(ctx, dim)
        out.append(
            DimensionSummary(
                dimension=dim,
                terms=len(terms),
                terms_used=sum(1 for t in terms if t.record_count),
                records_tagged=0,
            )
        )
    tagged: dict[str, int] = {}
    total = 0
    with ctx.catalogue() as con:
        if _has_table(con, "records"):
            row = con.execute("SELECT count(*) FROM records").fetchone()
            total = int(row[0]) if row else 0
        if _has_table(con, "tags"):
            tagged = dict(
                con.execute(
                    "SELECT dimension, count(DISTINCT record_id) FROM tags "
                    "WHERE confidence >= ? GROUP BY dimension",
                    [Filters().min_confidence],
                ).fetchall()
            )
    out = [
        DimensionSummary(
            s.dimension, s.terms, s.terms_used, int(tagged.get(s.dimension.value, 0))
        )
        for s in out
    ]
    return out, total
