"""Turn the shared :class:`~chatcde.schemas.Filters` into a set of record IDs.

Every search and count applies the same filters, so they are resolved here once:
the result is the list of records that match, which the retrievers then take as
``record_ids`` (see :func:`chatcde.store.embeddings.vector_search` and
:func:`chatcde.store.bm25.bm25_search`). B5's ``count_by``, ``find_gaps`` and
``coverage_report`` reuse it.

Semantics (docs/decisions/0013-hybrid-search.md):

* Fields combine with AND; the values in one list combine with OR.
* ``year_from`` / ``year_to`` are inclusive; records without a year fail them.
* ``pub_types``, ``languages`` and ``cde_units`` match the ``records`` columns,
  ignoring case.
* Every other list field matches the tags of one dimension (``TAG_FIELDS``:
  ``impact_areas`` -> ``impact_area``, ``methods`` -> ``method``, …) with
  ``confidence >= min_confidence``, ignoring case. Every tagged dimension has a
  field (decision 0034).

Codes are checked against the vocabulary before they reach this module
(:func:`chatcde.tools.vocabulary.check_filter_codes`), so a misspelt code fails
instead of matching nothing.
"""

from __future__ import annotations

import duckdb

from chatcde.schemas import Dimension, Filters


class FilterError(Exception):
    """The filters ask for data the catalogue does not have (yet)."""


#: Filters fields that are answered from the ``tags`` table, by dimension.
TAG_FIELDS: dict[str, Dimension] = {
    "impact_areas": Dimension.IMPACT_AREA,
    "sub_areas": Dimension.SUB_AREA,
    "additional_sub_areas": Dimension.ADDITIONAL_SUB_AREA,
    "objectives": Dimension.OBJECTIVE,
    "pathways": Dimension.PATHWAY,
    "research_modes": Dimension.RESEARCH_MODE,
    "transformative_aspirations": Dimension.TRANSFORMATIVE_ASPIRATION,
    "methods": Dimension.METHOD,
    "data_types": Dimension.DATA_TYPE,
    "countries": Dimension.COUNTRY,
    "regions": Dimension.REGION,
    "settings": Dimension.SETTING,
    "scales": Dimension.SCALE,
    "systems": Dimension.SYSTEM,
    "actors": Dimension.ACTORS,
    "justice_lenses": Dimension.JUSTICE_LENS,
    "sdgs": Dimension.SDG,
    "output_types": Dimension.OUTPUT_TYPE,
}


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """True if the catalogue has a table called ``name``."""
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = ?"
    row = con.execute(sql, [name]).fetchone()
    return bool(row and row[0])


def _lower(values: list[str] | list[int]) -> list[str]:
    """Values as lower-case strings, for case-insensitive matching."""
    return [str(v).strip().lower() for v in values]


def is_empty(filters: Filters | None) -> bool:
    """True if ``filters`` restrict nothing (``min_confidence`` alone does not)."""
    if filters is None:
        return True
    return (
        filters.year_from is None
        and filters.year_to is None
        and not filters.pub_types
        and not filters.languages
        and not filters.cde_units
        and not any(getattr(filters, f) for f in TAG_FIELDS)
    )


def filter_record_ids(
    con: duckdb.DuckDBPyConnection, filters: Filters | None
) -> list[str] | None:
    """Return the IDs of the records that match ``filters``, sorted.

    Args:
        con: Open catalogue (read-only is enough).
        filters: The restrictions; ``None`` or empty means none.

    Returns:
        ``None`` when nothing is restricted (search everything), otherwise the
        matching record IDs (possibly an empty list).

    Raises:
        FilterError: If a tag filter is set but the catalogue has no tags yet.
    """
    if filters is None or is_empty(filters):
        return None
    clauses: list[str] = []
    params: list[object] = []
    if filters.year_from is not None:
        clauses.append("r.year >= ?")
        params.append(filters.year_from)
    if filters.year_to is not None:
        clauses.append("r.year <= ?")
        params.append(filters.year_to)
    if filters.pub_types:
        clauses.append("lower(r.pub_type) IN (SELECT unnest(?::VARCHAR[]))")
        params.append(_lower(filters.pub_types))
    for column in ("languages", "cde_units"):
        values = getattr(filters, column)
        if values:
            clauses.append(
                f"EXISTS (SELECT 1 FROM (SELECT unnest(r.{column}) AS v) "
                "WHERE lower(v) IN (SELECT unnest(?::VARCHAR[])))"
            )
            params.append(_lower(values))

    tag_fields = [f for f in TAG_FIELDS if getattr(filters, f)]
    if tag_fields and not _has_table(con, "tags"):
        raise FilterError(
            f"filters on {', '.join(tag_fields)} need tags, but the catalogue has "
            "none yet; run `chatcde tag` or `chatcde fetch-data`"
        )
    for field in tag_fields:
        clauses.append(
            "r.record_id IN (SELECT record_id FROM tags WHERE dimension = ? "
            "AND lower(term) IN (SELECT unnest(?::VARCHAR[])) AND confidence >= ?)"
        )
        params += [
            TAG_FIELDS[field].value,
            _lower(getattr(filters, field)),
            filters.min_confidence,
        ]

    sql = (
        "SELECT r.record_id FROM records r WHERE "
        + " AND ".join(clauses)
        + " ORDER BY r.record_id"
    )
    return [rid for (rid,) in con.execute(sql, params).fetchall()]
