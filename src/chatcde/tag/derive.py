"""Publish a tagging run to the shared ``tags`` table, with derived dimensions.

``tag_runs`` keeps every run (``main``, ``alt``, …) for evaluation (#16). The
tools read ``tags``, which holds one run: :func:`publish` copies that run's tags
and adds the dimensions that are derived in code, never tagged (decisions 0017,
0027, 0028):

* **region**, from each country tag (:class:`chatcde.tag.regions.RegionMap`):
  its UN M49 world region, its CDE partner region if any, and
  ``cde_partner_country`` for the seven partner countries;
* **impact_area**, from the parents of the sub_area tags (SL-a → SL) and of the
  additional_sub_area tags whose ``impact_area`` is JE, SL, SG or TE
  (``cross_cutting`` and ``outside`` give none).

A derived tag takes the highest confidence of the tags it comes from, and that
tag's source; it has no quote. Its provenance names the model ``derived`` and the
prompt :data:`DERIVE_VERSION`, so it is never mistaken for an LLM tag.

The whole table is replaced in one transaction, so readers never see half a run.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import duckdb
from pydantic import BaseModel

from chatcde import catalogue
from chatcde.config import Config
from chatcde.harvest.common import code_version, now
from chatcde.schemas import Dimension, Provenance, Tag, TextSource
from chatcde.tag import tables
from chatcde.tag.regions import RegionMap, load_countries
from chatcde.tag.vocabulary import load_vocabulary, unit_codes

log = logging.getLogger(__name__)

DERIVE_VERSION = "derive.v1"
IMPACT_AREAS = ("JE", "SL", "SG", "TE")


class PublishSummary(BaseModel):
    """What :func:`publish` wrote, and how derived impact areas match BORIS units."""

    run: str
    records_tagged: int
    llm_tags: int
    region_tags: int
    impact_area_tags: int
    records_with_unit: int  # records with a BORIS CDE unit that maps to an area
    unit_agrees: int  # ... whose derived impact areas include that unit's area


def _derived(
    best: dict[tuple[str, str], tuple[float, TextSource]],
    dimension: Dimension,
    provenance: Provenance,
) -> list[Tag]:
    """Tags of a derived dimension from (record, term) -> (confidence, source)."""
    return [
        Tag(
            record_id=rid,
            dimension=dimension,
            term=term,
            confidence=conf,
            evidence=None,
            source=source,
            provenance=provenance,
        )
        for (rid, term), (conf, source) in sorted(best.items())
    ]


def _keep_best(
    best: dict[tuple[str, str], tuple[float, TextSource]],
    key: tuple[str, str],
    confidence: float,
    source: TextSource,
) -> None:
    """Remember the highest-confidence origin of a derived (record, term)."""
    if key not in best or confidence > best[key][0]:
        best[key] = (confidence, source)


def derive(tags: list[Tag], vocab: dict[str, Any], regions: RegionMap) -> list[Tag]:
    """Region and impact_area tags derived from ``tags`` (see module docstring)."""
    asa_parent = {
        t["code"]: t.get("impact_area")
        for t in (vocab.get("additional_sub_area") or {}).get("terms") or []
    }
    region: dict[tuple[str, str], tuple[float, TextSource]] = {}
    area: dict[tuple[str, str], tuple[float, TextSource]] = {}
    for t in tags:
        if t.dimension is Dimension.COUNTRY:
            for term in regions.terms(t.term):
                _keep_best(region, (t.record_id, term), t.confidence, t.source)
        elif t.dimension is Dimension.SUB_AREA:
            parent = t.term.split("-")[0]
            if parent in IMPACT_AREAS:
                _keep_best(area, (t.record_id, parent), t.confidence, t.source)
        elif t.dimension is Dimension.ADDITIONAL_SUB_AREA:
            parent = asa_parent.get(t.term)
            if parent in IMPACT_AREAS:
                _keep_best(area, (t.record_id, parent), t.confidence, t.source)
    provenance = Provenance(
        code_version=code_version(),
        created_at=now(),
        model="derived",
        prompt_version=DERIVE_VERSION,
        vocab_version=str(vocab.get("version")),
    )
    return _derived(region, Dimension.REGION, provenance) + _derived(
        area, Dimension.IMPACT_AREA, provenance
    )


def run_tags(con: duckdb.DuckDBPyConnection, run: str) -> list[Tag]:
    """The LLM tags of one run, as :class:`Tag` (run and batch dropped)."""
    tables.ensure(con, "tag_runs")
    fields = list(Tag.model_fields)
    cur = con.execute(
        f"SELECT {', '.join(fields)} FROM tag_runs WHERE run = ? "
        "ORDER BY record_id, dimension, term",
        [run],
    )
    return [
        Tag.model_validate(dict(zip(fields, row, strict=True)))
        for row in cur.fetchall()
    ]


def unit_agreement(
    con: duckdb.DuckDBPyConnection,
    derived: list[Tag],
    vocab: dict[str, Any],
    min_confidence: float = 0.5,
) -> tuple[int, int]:
    """(records with a CDE unit that maps to an area, those whose areas include it).

    Only derived impact areas at ``min_confidence`` or more count, as in the tools.
    """
    codes = unit_codes(vocab)
    areas: dict[str, set[str]] = defaultdict(set)
    for t in derived:
        if t.dimension is Dimension.IMPACT_AREA and t.confidence >= min_confidence:
            areas[t.record_id].add(t.term)
    with_unit = agrees = 0
    for rid, units in con.execute(
        "SELECT record_id, cde_units FROM records WHERE len(cde_units) > 0"
    ).fetchall():
        wanted = {codes[u] for u in units or [] if u in codes}
        if not wanted:
            continue
        with_unit += 1
        agrees += bool(wanted & areas.get(rid, set()))
    return with_unit, agrees


def publish(
    con: duckdb.DuckDBPyConnection, cfg: Config, run: str | None = None
) -> PublishSummary:
    """Replace ``tags`` with one run's tags plus the derived dimensions.

    Raises:
        RuntimeError: The run has no tags (nothing would be published).
    """
    run = run or cfg.tagging.run
    llm = run_tags(con, run)
    if not llm:
        raise RuntimeError(f"run '{run}' has no tags; nothing to publish")
    vocab = load_vocabulary(cfg.vocab.file)
    regions = RegionMap(load_countries(cfg.tagging.m49_file), vocab)
    derived = derive(llm, vocab, regions)
    catalogue.replace_tables(con, {"tags": [*llm, *derived]})
    with_unit, agrees = unit_agreement(con, derived, vocab)
    summary = PublishSummary(
        run=run,
        records_tagged=len({t.record_id for t in llm}),
        llm_tags=len(llm),
        region_tags=sum(t.dimension is Dimension.REGION for t in derived),
        impact_area_tags=sum(t.dimension is Dimension.IMPACT_AREA for t in derived),
        records_with_unit=with_unit,
        unit_agrees=agrees,
    )
    log.info(
        "Published run %s: %d LLM tags + %d region + %d impact_area tags",
        run,
        summary.llm_tags,
        summary.region_tags,
        summary.impact_area_tags,
    )
    return summary
