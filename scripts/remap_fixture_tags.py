"""Remap the hand-made fixture tags from term labels to vocabulary codes.

One-off script for vocabulary v0.3 (issue #14, decisions 0026 and 0027). Up to
v0.2, ``Tag.term`` held the label of a closed-list term ("Case study"); from
v0.3 it holds the code ("MET-case-study"). The fixture tags in
``tests/fixtures/tags.jsonl`` and their hand-check rows in ``tag_review.csv``
were written against v0.2, so this script rewrites their terms with the code map
that ``chatcde vocab apply`` writes (``vocab/curation/v0.3_codes.csv``).

What it does, and why:
- Kept terms map to their code; merged terms map to the code they were merged
  into (the hand-checked meaning is unchanged, only its name).
- A retired term has no replacement in the code map. It needs a human choice,
  given in ``OVERRIDES``; without one the script stops and lists the tags.
- If two tags of a record end up with the same code (a merge), the one with the
  higher confidence is kept, and the other's review row is dropped with it.
- ``provenance.vocab_version`` becomes the new version; the hand-check status is
  kept, with a note saying the term was remapped.

Run from the repository root, then rebuild the fixture database::

    python scripts/remap_fixture_tags.py --dry-run
    python scripts/remap_fixture_tags.py
    python tests/fixtures/build_fixture_db.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

FIX = Path("tests/fixtures")

#: Retired terms with no ``replaced_by``: (dimension, old label) -> code.
OVERRIDES: dict[tuple[str, str], str] = {
    # Futures methods was retired in v0.3; the synthetic living-lab record's
    # visioning workshop is now covered by scenario development.
    (
        "method",
        "Futures methods (visioning, scenarios, futures workshops)",
    ): "MET-scenario-development-and-analysis",
}

NOTE = "remapped to v0.3 code"


def load_code_map(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """(dimension, old label) -> (code, how) from the CSV ``vocab apply`` writes."""
    with path.open(encoding="utf-8", newline="") as f:
        return {
            (r["dimension"], r["old_label"]): (r["code"], r["how"])
            for r in csv.DictReader(f)
        }


def new_term(
    dimension: str, term: str, code_map: dict[tuple[str, str], tuple[str, str]]
) -> str | None:
    """The code for one tag term; the term itself if its dimension is not curated.

    Returns None for a retired term with no override (needs a human choice).
    """
    curated = {d for d, _ in code_map}
    if dimension not in curated or term == "other":
        return term
    if (dimension, term) in OVERRIDES:
        return OVERRIDES[(dimension, term)]
    code, how = code_map.get((dimension, term), ("", "missing"))
    if how == "retired" or not code:
        return None
    return code


def main() -> int:
    """Remap tags.jsonl and tag_review.csv; return a process exit code."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--code-map", type=Path, default=Path("vocab/curation/v0.3_codes.csv")
    )
    ap.add_argument("--version", default="0.3")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    code_map = load_code_map(args.code_map)
    tags = [
        json.loads(line)
        for line in (FIX / "tags.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stuck, renamed = [], {}  # renamed: (record, dim, old term, role) -> new term
    for t in tags:
        code = new_term(t["dimension"], t["term"], code_map)
        if code is None:
            stuck.append((t["record_id"], t["dimension"], t["term"]))
        elif code != t["term"]:
            renamed[(t["record_id"], t["dimension"], t["term"], t["role"])] = code
    if stuck:
        print("No code for these tags; add them to OVERRIDES:", *stuck, sep="\n  ")
        return 1

    # Apply, dropping duplicates created by merges (keep the higher confidence).
    best: dict[tuple[str, str, str, str | None], dict] = {}
    dropped: set[tuple[str, str, str, str | None]] = set()
    for t in tags:
        old_key = (t["record_id"], t["dimension"], t["term"], t["role"])
        t["term"] = renamed.get(old_key, t["term"])
        t["provenance"]["vocab_version"] = args.version
        key = (t["record_id"], t["dimension"], t["term"], t["role"])
        if key in best:
            keep, lose = sorted([best[key], t], key=lambda x: -x["confidence"])
            best[key] = keep
            dropped.add(old_key)
        else:
            best[key] = t

    with (FIX / "tag_review.csv").open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        review = list(reader)
    # A dropped duplicate's review row would share its key with the kept one.
    review = [
        r
        for r in review
        if (r["record_id"], r["dimension"], r["term"], r["role"] or None) not in dropped
    ]
    for r in review:
        old_key = (r["record_id"], r["dimension"], r["term"], r["role"] or None)
        if old_key in renamed:
            r["term"] = renamed[old_key]
            r["note"] = "; ".join(x for x in (r["note"], NOTE) if x)

    print(
        f"{len(renamed)} of {len(tags)} tags remapped, "
        f"{len(dropped)} duplicates dropped"
    )
    if args.dry_run:
        return 0
    (FIX / "tags.jsonl").write_text(
        "".join(
            json.dumps(t, ensure_ascii=False, separators=(",", ":")) + "\n"
            for t in best.values()
        ),
        encoding="utf-8",
    )
    with (FIX / "tag_review.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\r\n")
        w.writeheader()
        w.writerows(review)
    return 0


if __name__ == "__main__":
    sys.exit(main())
