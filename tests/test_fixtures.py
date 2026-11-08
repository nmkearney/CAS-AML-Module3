"""Checks on tests/fixtures/: schema validity, integrity, vocabulary, credits, review.

The fixtures are the stand-in catalogue Track B builds against (issue #3), so
these tests guard that they match ``chatcde.schemas`` and the draft vocabulary,
that every real record is credited with its licence, and that every tag has been
checked by hand (``tag_review.csv``).
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import build_fixture_db as bdb
import duckdb
import pytest
import yaml

from chatcde.schemas import Dimension, Record, Tag

FIX = Path(__file__).parent / "fixtures"
VOCAB = Path(__file__).parents[1] / "vocab" / "draft_vocabulary.yaml"
SYNTHETIC = re.compile(r"^fixture-synthetic-\d{2}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# UN M49 regions and sub-regions, for world-level `region` tags (the vocabulary
# lists only the CDE partner regions by name).
M49 = {
    "Africa", "Northern Africa", "Sub-Saharan Africa", "Eastern Africa",
    "Middle Africa", "Southern Africa", "Western Africa", "Americas",
    "Latin America and the Caribbean", "Caribbean", "Central America",
    "South America", "Northern America", "Asia", "Central Asia", "Eastern Asia",
    "South-eastern Asia", "Southern Asia", "Western Asia", "Europe",
    "Eastern Europe", "Northern Europe", "Southern Europe", "Western Europe",
    "Oceania", "Australia and New Zealand", "Melanesia", "Micronesia", "Polynesia",
}  # fmt: skip


def rows(name: str) -> list[Any]:
    """All validated rows of one fixture table."""
    file_name, model = bdb.TABLES[name]
    return bdb.load_jsonl(FIX / file_name, model)


def flatten(node: Any) -> set[str]:
    """Codes (or labels where there are no codes) from a vocabulary YAML node."""
    if isinstance(node, list):
        out: set[str] = set()
        for x in node:
            out |= {x["code"]} if isinstance(x, dict) else {str(x)}
        return out
    if isinstance(node, dict):
        if "terms" in node:  # curated layout (vocab apply, decision 0025)
            return {t["code"] for t in node["terms"]}
        return set().union(*(flatten(v) for v in node.values()))
    return set()


def allowed_terms() -> dict[Dimension, set[str] | None]:
    """Allowed ``Tag.term`` values per dimension; None means checked by pattern."""
    v = yaml.safe_load(VOCAB.read_text(encoding="utf-8"))
    terms: dict[Dimension, set[str] | None] = {}
    for d in Dimension:
        node = v[d.value]
        if d is Dimension.COUNTRY or d is Dimension.SDG:
            terms[d] = None
        elif d is Dimension.REGION:
            terms[d] = set(node["cde_partner_region"]) | M49
        elif d is Dimension.ACTORS and "terms" not in node:
            terms[d] = flatten(node["groups"])
        elif d is Dimension.OUTPUT_TYPE:
            terms[d] = set(node["audience"])
        else:
            terms[d] = flatten(node)
    return terms


# --- Records and links ---------------------------------------------------------


def test_every_table_validates() -> None:
    for name in bdb.TABLES:
        rows(name)  # raises with file:line on the first invalid line


def test_record_counts_and_ids() -> None:
    recs = rows("records")
    ids = [r.record_id for r in recs]
    assert len(ids) == len(set(ids)), "duplicate record_id"
    real = [i for i in ids if UUID.match(i)]
    synth = [i for i in ids if SYNTHETIC.match(i)]
    assert len(real) + len(synth) == len(ids), "ids must be BORIS UUIDs or synthetic"
    assert 28 <= len(real) <= 32
    assert 3 <= len(synth) <= 4


@pytest.mark.parametrize("table", ["authors", "tags", "pages", "downloads"])
def test_rows_point_to_records(table: str) -> None:
    ids = {r.record_id for r in rows("records")}
    orphans = {r.record_id for r in rows(table)} - ids
    assert not orphans, f"{table} rows for unknown records: {orphans}"


def test_one_download_per_record() -> None:
    counts = Counter(r.record_id for r in rows("downloads"))
    assert set(counts) == {r.record_id for r in rows("records")}
    assert max(counts.values()) == 1


def test_authors_positions_are_contiguous() -> None:
    by_rec: dict[str, list[int]] = {}
    for a in rows("authors"):
        by_rec.setdefault(a.record_id, []).append(a.position)
    for rid, pos in by_rec.items():
        assert sorted(pos) == list(range(len(pos))), rid


def test_fulltext_flag_matches_pages() -> None:
    with_pages = {p.record_id for p in rows("pages")}
    for r in rows("records"):
        assert r.has_fulltext == (r.record_id in with_pages), r.record_id


def test_synthetic_edge_cases_present() -> None:
    synth: list[Record] = [r for r in rows("records") if SYNTHETIC.match(r.record_id)]
    assert any(r.access.value == "restricted" for r in synth)
    assert any(r.access.value == "metadata_only" for r in synth)
    assert any(r.abstract is None for r in synth)
    assert any(r.doi is None for r in synth)
    assert any(r.year is None for r in synth)


def test_real_records_were_selected_and_kept() -> None:
    with (FIX / "selection.csv").open(encoding="utf-8", newline="") as f:
        selected = {r["uuid"] for r in csv.DictReader(f)}
    with (FIX / "raw" / "fetch_log.csv").open(encoding="utf-8", newline="") as f:
        kept = {r["uuid"] for r in csv.DictReader(f) if r["status"] == "kept"}
    real = {r.record_id for r in rows("records") if UUID.match(r.record_id)}
    assert real <= selected
    assert real == kept


# --- Tags ----------------------------------------------------------------------


def test_every_record_has_tags() -> None:
    tagged = {t.record_id for t in rows("tags")}
    missing = {r.record_id for r in rows("records")} - tagged
    assert not missing, f"records without tags: {sorted(missing)}"


def test_tags_use_the_vocabulary() -> None:
    allowed = allowed_terms()
    bad = []
    for t in rows("tags"):
        if t.term == "other":
            if not t.other_label:
                bad.append((t.record_id, t.dimension.value, "other without label"))
            continue
        terms = allowed[t.dimension]
        if t.dimension is Dimension.COUNTRY:
            ok = bool(re.fullmatch(r"[A-Z]{3}", t.term)) or (
                t.term == "global_or_multi_country"
            )
        elif t.dimension is Dimension.SDG:
            ok = t.term.isdigit() and 1 <= int(t.term) <= 17
        else:
            ok = terms is not None and t.term in terms
        if not ok:
            bad.append((t.record_id, t.dimension.value, t.term))
    assert not bad, f"terms not in vocabulary: {bad}"


def test_tag_rules() -> None:
    sdgs: Counter[str] = Counter()
    seen: set[tuple[str, str, str, str | None]] = set()
    for t in rows("tags"):
        is_actor = t.dimension is Dimension.ACTORS
        assert (t.role is not None) == is_actor, (t.record_id, t.dimension, t.role)
        key = (t.record_id, t.dimension.value, t.term, t.role and t.role.value)
        assert key not in seen, f"duplicate tag {key}"
        seen.add(key)
        if t.dimension is Dimension.SDG:
            sdgs[t.record_id] += 1
        assert t.provenance.vocab_version, "tags must record the vocabulary version"
    assert all(n <= 3 for n in sdgs.values()), sdgs


def test_tags_cover_all_dimensions() -> None:
    used = {t.dimension for t in rows("tags")}
    assert used == set(Dimension), f"unused: {set(Dimension) - used}"


def test_fixtures_include_low_confidence_and_other() -> None:
    tags: list[Tag] = rows("tags")
    assert any(t.confidence < 0.5 for t in tags)
    assert any(t.term == "other" for t in tags)


def _norm(s: str) -> str:
    """Normalise text for quote matching: NFKC (ligatures), no soft hyphens, spaces."""
    s = unicodedata.normalize("NFKC", s).replace("\xad", "")
    return " ".join(s.split()).casefold()


def evidence_found(tag: Tag, record: dict[str, Any]) -> bool:
    """True if the tag's evidence quote occurs in the text named by its source.

    ``fulltext`` means the record's pages; ``abstract_*`` the abstract;
    ``metadata`` the title and keywords. Tags without evidence pass.
    """
    if not tag.evidence:
        return True
    src = tag.source.value
    if src == "fulltext":
        pages = [p.text for p in rows("pages") if p.record_id == tag.record_id]
        text = " ".join(pages)
    elif src.startswith("abstract"):
        text = record.get("abstract") or ""
    else:
        text = " ".join([record["title"], *record.get("keywords", [])])
    return _norm(tag.evidence) in _norm(text)


def test_evidence_quotes_are_verbatim() -> None:
    recs = {r.record_id: r.model_dump() for r in rows("records")}
    bad = [
        (t.record_id, t.dimension.value, t.term)
        for t in rows("tags")
        if not evidence_found(t, recs[t.record_id])
    ]
    assert not bad, f"evidence not found in source text: {bad}"


def _review_key(r: dict[str, str]) -> tuple[str, str, str, str | None]:
    return (r["record_id"], r["dimension"], r["term"], r["role"] or None)


def test_every_tag_checked_by_hand() -> None:
    """The 'done when' gate of issue #3: every tag reviewed in tag_review.csv."""
    with (FIX / "tag_review.csv").open(encoding="utf-8", newline="") as f:
        review = {_review_key(r): r for r in csv.DictReader(f)}
    unchecked = []
    for t in rows("tags"):
        key = (t.record_id, t.dimension.value, t.term, t.role and t.role.value)
        r = review.get(key)
        if r is None or r["status"] not in ("ok", "changed") or not r["checked_by"]:
            unchecked.append(key)
    assert not unchecked, f"{len(unchecked)} tags not checked by hand"
    removed = {k for k, r in review.items() if r["status"] == "removed"}
    live = {
        (t.record_id, t.dimension.value, t.term, t.role and t.role.value)
        for t in rows("tags")
    }
    assert not removed & live, "tags marked removed are still in tags.jsonl"


# --- Credits -------------------------------------------------------------------


def test_readme_credits_every_real_record() -> None:
    readme = (FIX / "README.md").read_text(encoding="utf-8")
    for d in sorted((FIX / "raw").iterdir()):
        if not (d / "fetch.json").exists():
            continue
        fetch = json.loads((d / "fetch.json").read_text(encoding="utf-8"))
        line = next((x for x in readme.splitlines() if d.name in x), None)
        assert line, f"README has no credit line for {d.name}"
        assert fetch["licence"] in line, f"README licence missing for {d.name}"


# --- DuckDB --------------------------------------------------------------------


def test_fixture_db_matches_jsonl(fixture_db: Path) -> None:
    con = duckdb.connect(str(fixture_db), read_only=True)
    try:
        for table in bdb.TABLES:
            (n,) = con.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert n == len(rows(table)), table
        (n,) = con.execute(
            "SELECT count(*) FROM records WHERE list_contains(cde_units, ?)",
            ["Sustainable Governance"],
        ).fetchone()
        assert n >= 1
    finally:
        con.close()
