"""Step 1 of ``chatcde vocab extract``: draw a stratified sample of records.

The vocabulary should reflect all of CDE's work, not just its largest groups, so
the sample is stratified by **unit x decade x language** (docs/planning.md,
vocabulary process; decision 0019):

* unit: the record's first CDE unit as an impact-area code (``JE``, ``SL``,
  ``SG``, ``TE``), a slug for units without one (``services``), or ``none``.
  Only about 550 of 3,003 records carry a unit at all.
* decade: ``2000s``, ``2010s``, ... with everything before
  ``vocab_extract.pool_before`` pooled as ``<2000``, and ``unknown`` for no year.
* language: the record's first language if it is listed in
  ``vocab_extract.languages`` (en, de), else ``other``.

Allocation is "balanced with floors": each non-empty stratum first gets
``min(stratum_floor, size)`` records, so small groups (German, units, older work)
are seen at all; the rest of ``sample_size`` is shared out in proportion to the
records left in each stratum (largest remainder). Within a stratum, records with
parsed pages come first, because they give the model more to go on.

Only records with an abstract or parsed pages are eligible: a title alone is
too thin to label. The draw is seeded and stored in ``vocab_sample``, so the same
settings always give the same sample and a re-run is skipped.
"""

from __future__ import annotations

import csv
import logging
import random
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import duckdb
from pydantic import BaseModel

from chatcde.config import Config
from chatcde.harvest.common import code_version, now
from chatcde.tag import tables
from chatcde.tag.vocabulary import load_vocabulary, unit_codes

log = logging.getLogger(__name__)


class SampleSummary(BaseModel):
    """What the sample step found and did, with its denominators."""

    publications: int
    eligible: int
    sampled: int
    reused: bool  # an existing sample with the same settings was kept
    strata: int  # non-empty strata among eligible records
    by_unit: dict[str, int]
    by_decade: dict[str, int]
    by_language: dict[str, int]
    with_pages: int


@dataclass(frozen=True)
class Candidate:
    """An eligible record and its stratum."""

    record_id: str
    unit: str
    decade: str
    language: str
    has_pages: bool

    @property
    def stratum(self) -> str:
        """Stratum key, ``unit|decade|language``."""
        return f"{self.unit}|{self.decade}|{self.language}"


def slug(text: str) -> str:
    """Lower-case ASCII slug, e.g. ``"Services" -> "services"``."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "unit"


def decade_of(year: int | None, pool_before: int) -> str:
    """Decade label: ``"2010s"``; years before ``pool_before`` give ``"<2000"``."""
    if year is None:
        return "unknown"
    if year < pool_before:
        return f"<{pool_before}"
    return f"{year // 10 * 10}s"


def unit_of(units: list[str] | None, codes: dict[str, str]) -> str:
    """Stratum unit for a record's BORIS units: the first one's code, or ``none``."""
    if not units:
        return "none"
    first = units[0]
    return codes.get(first) or slug(first)


def language_of(languages: list[str] | None, own: list[str]) -> str:
    """Stratum language: the first language if it has a stratum, else ``other``."""
    if languages and languages[0] in own:
        return languages[0]
    return "other"


def load_candidates(
    con: duckdb.DuckDBPyConnection, cfg: Config
) -> tuple[int, list[Candidate]]:
    """Return (publications in the catalogue, eligible records with their strata).

    A record is eligible if it has a non-empty abstract or at least one page of
    text in ``pages`` (if that table exists; it is written by ``chatcde parse``).
    """
    codes = unit_codes(load_vocabulary(cfg.vocab.file))
    total = con.execute("SELECT count(*) FROM records").fetchone()[0]  # type: ignore[index]
    if tables.has_table(con, "pages"):
        pages_sql = "SELECT DISTINCT record_id FROM pages WHERE length(trim(text)) > 0"
    else:
        pages_sql = "SELECT NULL::VARCHAR AS record_id WHERE false"
    rows = con.execute(
        f"""
        WITH p AS ({pages_sql})
        SELECT r.record_id, r.year, r.languages, r.cde_units,
               p.record_id IS NOT NULL AS has_pages,
               coalesce(length(trim(r.abstract)), 0) > 0 AS has_abstract
        FROM records r LEFT JOIN p USING (record_id)
        ORDER BY r.record_id
        """
    ).fetchall()
    out = [
        Candidate(
            record_id=rid,
            unit=unit_of(units, codes),
            decade=decade_of(year, cfg.vocab_extract.pool_before),
            language=language_of(langs, cfg.vocab_extract.languages),
            has_pages=bool(has_pages),
        )
        for rid, year, langs, units, has_pages, has_abstract in rows
        if has_pages or has_abstract
    ]
    return int(total), out


def allocate(sizes: dict[str, int], target: int, floor: int) -> dict[str, int]:
    """How many records to draw from each stratum.

    Each stratum gets ``min(floor, size)``; the remainder of ``target`` is shared
    in proportion to what is left in each stratum, rounding by largest
    remainder (ties broken by stratum name, so the result is deterministic).
    If the floors alone exceed ``target``, the floors are kept and the sample is
    larger than asked; if there are fewer records than ``target``, all are taken.
    """
    alloc = {s: min(floor, n) for s, n in sizes.items()}
    left = target - sum(alloc.values())
    room = {s: sizes[s] - alloc[s] for s in sizes}
    capacity = sum(room.values())
    if left <= 0 or capacity == 0:
        return alloc
    if capacity <= left:
        return dict(sizes)
    shares = {s: left * room[s] / capacity for s in sizes}
    for s, q in shares.items():
        alloc[s] += int(q)
    rest = left - sum(int(q) for q in shares.values())
    by_remainder = sorted(sizes, key=lambda s: (-(shares[s] - int(shares[s])), s))
    for s in by_remainder:
        if rest == 0:
            break
        if alloc[s] < sizes[s]:
            alloc[s] += 1
            rest -= 1
    return alloc


def draw(
    candidates: list[Candidate], target: int, floor: int, seed: int
) -> list[Candidate]:
    """Draw the stratified sample (see the module docstring); seeded, so repeatable."""
    strata: dict[str, list[Candidate]] = {}
    for c in sorted(candidates, key=lambda c: c.record_id):
        strata.setdefault(c.stratum, []).append(c)
    alloc = allocate({s: len(v) for s, v in strata.items()}, target, floor)
    rng = random.Random(seed)
    picked: list[Candidate] = []
    for s in sorted(strata):
        members = list(strata[s])
        rng.shuffle(members)
        members.sort(
            key=lambda c: not c.has_pages
        )  # stable: pages first, random within
        picked.extend(members[: alloc[s]])
    return picked


def _summary(
    total: int, candidates: list[Candidate], picked: list[Candidate], reused: bool
) -> SampleSummary:
    """Summarise a sample against the eligible records."""

    def count(attr: str) -> dict[str, int]:
        return dict(sorted(Counter(getattr(c, attr) for c in picked).items()))

    return SampleSummary(
        publications=total,
        eligible=len(candidates),
        sampled=len(picked),
        reused=reused,
        strata=len({c.stratum for c in candidates}),
        by_unit=count("unit"),
        by_decade=count("decade"),
        by_language=count("language"),
        with_pages=sum(c.has_pages for c in picked),
    )


def _write_csv(path: Path, rows: list[tables.SampleRow]) -> None:
    """Write the sample as CSV for inspection (not used by later steps)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "stratum", "unit", "decade", "language", "has_pages"])
        for r in rows:
            w.writerow(
                [r.record_id, r.stratum, r.unit, r.decade, r.language, r.has_pages]
            )


def build_sample(
    con: duckdb.DuckDBPyConnection, cfg: Config, *, force: bool = False
) -> SampleSummary:
    """Draw the sample into ``vocab_sample`` unless one with the same settings exists.

    The stored sample is kept as long as ``seed``, ``sample_size`` and
    ``stratum_floor`` are unchanged, even if the catalogue grows, so the labels
    already written stay valid. A change of settings, or ``force``, draws anew.
    """
    s = cfg.vocab_extract
    tables.ensure(con, "vocab_sample")
    total, candidates = load_candidates(con, cfg)
    stored = con.execute(
        "SELECT DISTINCT seed, sample_size, stratum_floor FROM vocab_sample"
    ).fetchall()
    if not force and stored == [(s.seed, s.sample_size, s.stratum_floor)]:
        ids = {
            r[0] for r in con.execute("SELECT record_id FROM vocab_sample").fetchall()
        }
        picked = [c for c in candidates if c.record_id in ids]
        log.info(
            "Sample of %d records with seed %d already drawn; kept", len(ids), s.seed
        )
        return _summary(total, candidates, picked, reused=True)
    if stored:
        log.info("Settings changed or --force: drawing a new sample")
    picked = draw(candidates, s.sample_size, s.stratum_floor, s.seed)
    stamp, version = now(), code_version()
    rows = [
        tables.SampleRow(
            record_id=c.record_id,
            stratum=c.stratum,
            unit=c.unit,
            decade=c.decade,
            language=c.language,
            has_pages=c.has_pages,
            seed=s.seed,
            sample_size=s.sample_size,
            stratum_floor=s.stratum_floor,
            sampled_at=stamp,
            code_version=version,
        )
        for c in picked
    ]
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM vocab_sample")
        tables.insert(con, "vocab_sample", rows)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    _write_csv(s.sample_csv, rows)
    log.info(
        "Sampled %d of %d eligible records (%d strata), seed %d",
        len(rows),
        len(candidates),
        len({c.stratum for c in candidates}),
        s.seed,
    )
    return _summary(total, candidates, picked, reused=False)
