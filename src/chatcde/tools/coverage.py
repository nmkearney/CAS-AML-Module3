"""How complete the catalogue is: the logic behind ``coverage_report``.

Every claim that something is rare or missing needs to know how much of the
corpus could be read at all (CLAUDE.md: counts come from code, with their
denominators). This module puts each record of a filtered set into exactly one
**text status** and counts them by year, type, language and access rights
(issue #11, decision 0015):

* ``fulltext``: the record has at least one parsed page with text (``pages``).
  If the catalogue has no parsed pages yet, the download flag
  ``records.has_fulltext`` stands in for it, and :func:`fulltext_basis` says so.
* ``abstract_only``: no full text, but an abstract (from BORIS or OpenAlex).
* ``metadata_only``: neither.

The functions take an open catalogue connection so they can be tested on the
fixtures. :func:`chatcde.tools.core.coverage_report` is the tool the agent calls;
``chatcde coverage`` prints the same numbers.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

import duckdb

from chatcde.schemas import BundleManifest, CoverageReport, Edition, Filters
from chatcde.store.filters import filter_record_ids

#: The three text statuses, from most to least text.
STATUSES = ("fulltext", "abstract_only", "metadata_only")

Breakdown = Literal["year", "type", "language", "access"]

#: SQL expression for each breakdown key, over the ``sel`` relation below.
#: A record counts under its first listed language only, so every breakdown
#: adds up to the total.
BREAKDOWNS: dict[Breakdown, str] = {
    "year": "coalesce(CAST(year AS VARCHAR), 'unknown')",
    "type": "pub_type",
    "language": "coalesce(lower(languages[1]), 'none')",
    "access": "CAST(access AS VARCHAR)",
}

#: File in ``data.dir`` that a downloaded bundle carries (``chatcde fetch-data``).
MANIFEST_NAME = "manifest.json"


def _has_rows(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """True if ``table`` exists and holds at least one row."""
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()
    if not exists or not exists[0]:
        return False
    return con.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone() == (True,)


def fulltext_basis(con: duckdb.DuckDBPyConnection) -> Literal["pages", "downloads"]:
    """Which data decides that a record has full text.

    ``pages`` once ``chatcde parse`` has written parsed pages: only text the
    assistant can actually read counts. Before that, ``downloads``: the
    ``has_fulltext`` flag set by ``chatcde harvest files``, which may overstate
    (scans without OCR, unparsed files).
    """
    return "pages" if _has_rows(con, "pages") else "downloads"


def _selection(
    con: duckdb.DuckDBPyConnection, filters: Filters | None
) -> tuple[str, list[object]]:
    """SQL for the ``sel`` relation: one row per matching record, with its status.

    Returns the SQL (a ``WITH`` clause) and its parameters.
    """
    ids = filter_record_ids(con, filters)
    where, params = "", []
    if ids is not None:
        where = "WHERE r.record_id IN (SELECT unnest(?::VARCHAR[]))"
        params.append(ids)
    if fulltext_basis(con) == "pages":
        has_text = (
            "r.record_id IN (SELECT record_id FROM pages WHERE length(trim(text)) > 0)"
        )
    else:
        has_text = "r.has_fulltext"
    sql = (
        "WITH sel AS (SELECT r.record_id, r.year, r.pub_type, r.languages, "
        f"r.access, CASE WHEN {has_text} THEN 'fulltext' "
        "WHEN r.abstract IS NOT NULL THEN 'abstract_only' "
        f"ELSE 'metadata_only' END AS status FROM records r {where})"
    )
    return sql, params


def _sorted(by: Breakdown, counts: dict[str, int]) -> dict[str, int]:
    """Years in order (``unknown`` last); other keys by count, largest first."""
    if by == "year":
        return dict(sorted(counts.items(), key=lambda kv: (not kv[0].isdigit(), kv[0])))
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def breakdown(
    con: duckdb.DuckDBPyConnection, filters: Filters | None, by: Breakdown
) -> dict[str, dict[str, int]]:
    """Count the matching records by one key, split by text status.

    Args:
        con: Open catalogue (read-only is enough).
        filters: Restrictions; ``None`` means the whole catalogue.
        by: ``year``, ``type``, ``language`` (first listed) or ``access``.

    Returns:
        Key → {``fulltext``, ``abstract_only``, ``metadata_only``} → records,
        keys ordered as in :func:`_sorted`. Statuses with no records are 0.

    Raises:
        chatcde.store.filters.FilterError: Tag filters set but no tags yet.
    """
    sel, params = _selection(con, filters)
    rows = con.execute(
        f"{sel} SELECT {BREAKDOWNS[by]} AS k, status, count(*) FROM sel GROUP BY ALL",
        params,
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for key, status, n in rows:
        out.setdefault(key, dict.fromkeys(STATUSES, 0))[status] = n
    order = _sorted(by, {k: sum(v.values()) for k, v in out.items()})
    return {k: out[k] for k in order}


def low_confidence_tags(con: duckdb.DuckDBPyConnection, filters: Filters | None) -> int:
    """Tags on the matching records whose confidence is below ``min_confidence``.

    0 when the catalogue has no tags yet (before ``chatcde tag``).
    """
    if not _has_rows(con, "tags"):
        return 0
    threshold = (filters or Filters()).min_confidence
    sel, params = _selection(con, filters)
    row = con.execute(
        f"{sel} SELECT count(*) FROM tags t JOIN sel USING (record_id) "
        "WHERE t.confidence < ?",
        [*params, threshold],
    ).fetchone()
    return row[0] if row else 0


def local_edition(data_dir: Path) -> Edition:
    """The edition of the local data: from a bundle's manifest, else ``full``.

    A downloaded bundle (``chatcde fetch-data``) has ``manifest.json`` in the
    data folder. Without one the data was built locally by the pipeline, which
    holds every full text the builder could download, as the full edition does.
    """
    path = data_dir / MANIFEST_NAME
    if not path.is_file():
        return Edition.FULL
    raw = json.loads(path.read_text(encoding="utf-8"))
    return BundleManifest.model_validate(raw).edition


def build_report(
    con: duckdb.DuckDBPyConnection, filters: Filters | None, edition: Edition
) -> CoverageReport:
    """Assemble the :class:`CoverageReport` for ``filters``.

    Args:
        con: Open catalogue (read-only is enough).
        filters: Restrictions; ``None`` means the whole catalogue.
        edition: Edition of the local data (:func:`local_edition`).

    Raises:
        chatcde.store.filters.FilterError: Tag filters set but no tags yet.
    """
    by = {key: breakdown(con, filters, key) for key in BREAKDOWNS}
    totals: Counter[str] = Counter()
    for split in by["year"].values():
        totals.update(split)
    return CoverageReport(
        edition=edition,
        filters=filters or Filters(),
        total_records=sum(totals.values()),
        with_fulltext=totals["fulltext"],
        abstract_only=totals["abstract_only"],
        metadata_only=totals["metadata_only"],
        low_confidence_tags=low_confidence_tags(con, filters),
        by_year={k: sum(v.values()) for k, v in by["year"].items()},
        by_language={k: sum(v.values()) for k, v in by["language"].items()},
        by_type={k: sum(v.values()) for k, v in by["type"].items()},
        by_access={k: sum(v.values()) for k, v in by["access"].items()},
    )
