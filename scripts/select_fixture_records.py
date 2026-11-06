#!/usr/bin/env python3
"""Choose about 30 BORIS records for ``tests/fixtures/``, spread across strata.

Reads ``data/cde_records.csv`` (written by ``scripts/boris_cde_inventory.py``) and
picks open-access publications so that every value on four axes is covered where
the pool allows it:

* ``unit``: first CDE unit listed, or ``none``
* ``decade``: ``<2000``, ``2000s``, ``2010s``, ``2020s``
* ``lang``: ``en``, ``de``, ``other`` (first language code)
* ``type``: BORIS ``dc.type``, lower-cased

Algorithm (deterministic for a given ``--seed``):

1. Greedy coverage: repeatedly take the candidate that covers the most axis values
   still missing, rarest values first; ties broken by the seeded shuffle.
2. Fill up to ``--n`` with the candidate whose values are most under-used
   relative to the square root of their pool size, so common values (English,
   articles) still dominate but rare ones are not drowned out. Alternates records
   with and without an abstract.
3. Pick ``--reserves`` more the same way. ``fetch_fixture_records.py`` uses them
   when a pick has no open licence or no text layer. On the first run about 40%
   of candidates had an accepted licence, hence the large default.

Open access in BORIS only means "free to read"; the licence is checked later by
the fetch script. Writes ``tests/fixtures/selection.csv`` and prints per-axis
counts plus the thin strata (axis values with at most one record in the pool).

    python scripts/select_fixture_records.py [--n 30] [--reserves 90] [--seed 3]
"""

from __future__ import annotations

import argparse
import collections
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AXES = ("unit", "decade", "lang", "type")


def decade(year: str) -> str:
    """Bucket a four-digit year string into a decade label."""
    y = int(year)
    if y < 2000:
        return "<2000"
    return f"{y // 10 * 10}s"


def strata(row: dict[str, str]) -> dict[str, str]:
    """Return the four axis values of one CSV row."""
    units = [u for u in row["cde_units"].split("; ") if u]
    lang = (row["language"].split("; ")[0] or "other").strip()
    return {
        "unit": units[0] if units else "none",
        "decade": decade(row["year"]),
        "lang": lang if lang in ("en", "de") else "other",
        "type": row["type"].lower(),
    }


def load_pool(csv_path: Path) -> list[dict[str, str]]:
    """Load open-access publications that have a title and a year."""
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    pool = []
    for r in rows:
        if r["entity_type"] != "Publication" or r["access"] != "open.access":
            continue
        if not r["title"].strip() or not r["year"][:4].isdigit():
            continue
        r = dict(r, year=r["year"][:4])
        r.update(strata(r))
        pool.append(r)
    return pool


def pick(
    pool: list[dict[str, str]], n: int, reserves: int, seed: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Choose ``n`` records plus ``reserves`` spares; see the module docstring."""
    rng = random.Random(seed)
    cands = pool[:]
    rng.shuffle(cands)
    freq = {a: collections.Counter(r[a] for r in pool) for a in AXES}
    missing = {(a, v) for a in AXES for v in freq[a]}
    used: dict[str, collections.Counter[str]] = {a: collections.Counter() for a in AXES}
    chosen: list[dict[str, str]] = []

    def take(r: dict[str, str], reason: str) -> None:
        cands.remove(r)
        chosen.append(dict(r, reason=reason))
        for a in AXES:
            used[a][r[a]] += 1
            missing.discard((a, r[a]))

    # 1. Greedy coverage, rarest values weigh most.
    while missing and len(chosen) < n:

        def gain(r: dict[str, str]) -> float:
            return sum(1 / freq[a][r[a]] for a in AXES if (a, r[a]) in missing)

        best = max(cands, key=gain)
        if gain(best) == 0:
            break
        covered = [f"{a}={best[a]}" for a in AXES if (a, best[a]) in missing]
        take(best, "covers " + ", ".join(covered))

    # 2./3. Balanced fill, alternating abstract / no abstract.
    def balance(r: dict[str, str], want_abs: bool) -> tuple[float, int]:
        score = sum(used[a][r[a]] / freq[a][r[a]] ** 0.5 for a in AXES)
        return (score, 0 if (r["has_abstract"] == "True") == want_abs else 1)

    while len(chosen) < n + reserves and cands:
        want_abs = len(chosen) % 2 == 0
        best = min(cands, key=lambda r: balance(r, want_abs))
        take(best, "balance")
    return chosen[:n], chosen[n:]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--csv", type=Path, default=ROOT / "data" / "cde_records.csv")
    ap.add_argument(
        "--out", type=Path, default=ROOT / "tests" / "fixtures" / "selection.csv"
    )
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--reserves", type=int, default=90)
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()

    pool = load_pool(args.csv)
    chosen, spare = pick(pool, args.n, args.reserves, args.seed)

    fields = ["role", "rank", "uuid", *AXES, "year", "has_abstract", "doi"]
    fields += ["title", "reason"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for role, rows in (("pick", chosen), ("reserve", spare)):
            for i, r in enumerate(rows, 1):
                w.writerow(dict(r, role=role, rank=i))

    print(f"Pool: {len(pool)} open-access publications; seed {args.seed}")
    print(f"Chose {len(chosen)} + {len(spare)} reserves -> {args.out}\n")
    for a in AXES:
        pool_c = collections.Counter(r[a] for r in pool)
        pick_c = collections.Counter(r[a] for r in chosen)
        print(f"{a}:")
        for v, c in pool_c.most_common():
            thin = "  <- thin" if c <= 1 else ""
            print(f"  {pick_c[v]:3d} picked / {c:5d} in pool  {v}{thin}")
    print("\nPicks:")
    for r in chosen:
        print(
            f"  {r['uuid'][:8]} {r['year']} {r['lang']:5} {r['type'][:16]:16} "
            f"{r['unit'][:22]:22} {r['title'][:60]}"
        )


if __name__ == "__main__":
    main()
