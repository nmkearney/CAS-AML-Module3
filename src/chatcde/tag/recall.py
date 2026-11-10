"""A recall check from a second model (``chatcde evaluate recall-sheet``, 0029).

Precision was hand-checked on the pilot (``review-sheet``), but recall was not:
a reviewer would have to read every record against the whole vocabulary. This
narrows the work to the tags a second model gives and the main run does not:

* **agreement** (all records both runs tagged, no review needed): per dimension,
  the counted tags (confidence >= 0.5) of each run, the tags in both, and their
  overlap. Low overlap marks dimensions where either model is unreliable.
* **candidates** (the reviewed records only): every counted tag of the second
  run that the main run does not count (same record, dimension, code and, for
  actors, role), with its quote. The reviewer marks each ``ok`` (the main run
  missed it), ``wrong`` (the second model is wrong) or ``unsure``. Decisions the
  earlier review already made on the same tag (e.g. on an uncounted main tag)
  are carried.

Ok candidates per dimension give a lower bound on what the main run misses: a
tag both models miss stays invisible. Tags the reviewer listed as missing are
marked when the second model found them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import Workbook, load_workbook

from chatcde.config import Config
from chatcde.tag.curation import _write_sheet
from chatcde.tag.review import DECISIONS, _key, _sheet_rows, label_lookup

COUNTS = 0.5  # confidence from which a tag counts in the tools

README = [
    "Recall check from a second model (chatcde evaluate recall-sheet; decision 0029).",
    "",
    "Sheet 'candidates': tags the second model gives (and counts) that the main run",
    "does not count, for the reviewed records. Judge each in the white columns:",
    "  ok = the main run missed a real tag; wrong = the second model is wrong;",
    "  unsure = cannot tell. Decisions already made in the review are carried.",
    "main_confidence: the main run's confidence when it gave the tag below 0.5.",
    "in_missing: you listed this tag as missing in the review.",
    "",
    "Sheet 'agreement': counted tags per dimension over all records both runs tagged.",
    "overlap = both / (main + alt - both). No review needed.",
    "Sheet 'records': the records, with their text, as in the review workbook.",
]


def _counted(
    con: duckdb.DuckDBPyConnection, run: str, ids: list[str] | None = None
) -> dict[tuple[str, str, str, str], tuple[float, str, str | None]]:
    """Key -> (confidence, evidence, other_label) of every tag of a run."""
    sql = (
        "SELECT record_id, dimension, term, role, confidence, evidence, other_label "
        "FROM tag_runs WHERE run = ?"
    )
    params: list[Any] = [run]
    if ids is not None:
        sql += " AND list_contains(?, record_id)"
        params.append(ids)
    return {
        _key(r[0], r[1], r[2], r[3]): (float(r[4]), r[5] or "", r[6])
        for r in con.execute(sql, params).fetchall()
    }


def agreement(
    con: duckdb.DuckDBPyConnection, main: str, alt: str
) -> list[dict[str, Any]]:
    """Per dimension: counted tags of each run, both, and overlap (Jaccard)."""
    both_ids = [
        r[0]
        for r in con.execute(
            "SELECT record_id FROM tag_state WHERE run = ? AND status = 'ok' "
            "INTERSECT SELECT record_id FROM tag_state WHERE run = ? AND status = 'ok'",
            [main, alt],
        ).fetchall()
    ]
    sets: dict[str, dict[str, set[tuple[str, ...]]]] = defaultdict(
        lambda: {"main": set(), "alt": set()}
    )
    for name, run in (("main", main), ("alt", alt)):
        for key, (conf, _, _) in _counted(con, run, both_ids).items():
            if conf >= COUNTS:
                sets[key[1]][name].add(key)
    rows = []
    for dim in sorted(sets):
        m, a = sets[dim]["main"], sets[dim]["alt"]
        both = len(m & a)
        union = len(m | a)
        rows.append(
            {
                "dimension": dim,
                "records": len(set(both_ids)),
                "main": len(m),
                "alt": len(a),
                "both": both,
                "only_main": len(m - a),
                "only_alt": len(a - m),
                "overlap": round(both / union, 2) if union else None,
            }
        )
    return rows


def export_recall(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    *,
    review: Path,
    alt_run: str = "alt",
    main_run: str | None = None,
    out: Path | None = None,
    force: bool = False,
) -> tuple[Path, int, dict[str, int]]:
    """Write the recall workbook; return (path, candidates, candidates per dimension).

    Args:
        con: The catalogue.
        cfg: Settings (tagging.run, tagging.review.dir, the vocabulary).
        review: The filled-in review workbook of the main run; its records are
            the ones checked, and its decisions and missing tags are reused.
        alt_run: The second model's run.
        main_run: The main run (default ``tagging.run``).
        out: Workbook path (default ``recall_<alt_run>.xlsx`` in the review dir).
        force: Overwrite an existing workbook.

    Raises:
        FileExistsError: The workbook exists and ``force`` is not set.
        RuntimeError: The second run has no tags for the reviewed records.
    """
    main_run = main_run or cfg.tagging.run
    path = out or cfg.tagging.review.dir / f"recall_{alt_run}.xlsx"
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; use --force to overwrite it")
    old = load_workbook(review, read_only=True)
    records = sorted(_sheet_rows(old, "records"), key=lambda x: int(x["record_no"]))
    judged = {
        _key(r["record_id"], r["dimension"], r["term"], r["role"]): r
        for r in _sheet_rows(old, "tags")
    }
    missing = {
        (str(r.get("record_no")), str(r.get("dimension")), str(r.get("term")))
        for r in _sheet_rows(old, "missing")
        if r.get("record_no")
    }
    old.close()
    ids = [str(r["record_id"]) for r in records]
    number = {str(r["record_id"]): int(r["record_no"]) for r in records}
    title = {str(r["record_id"]): str(r.get("title") or "")[:80] for r in records}

    main = _counted(con, main_run, ids)
    alt = _counted(con, alt_run, ids)
    if not alt:
        raise RuntimeError(f"run '{alt_run}' has no tags for the reviewed records")
    labels = label_lookup(cfg)
    order = {
        d: i for i, d in enumerate(d for b in cfg.tagging.batches.values() for d in b)
    }
    rows: list[dict[str, Any]] = []
    for key, (conf, evidence, other) in alt.items():
        rid, dim, term, role = key
        if conf < COUNTS or main.get(key, (0.0,))[0] >= COUNTS:
            continue
        prev = judged.get(key) or {}
        rows.append(
            {
                "decision": prev.get("decision") or "",
                "note": prev.get("note") or "",
                "record_no": number[rid],
                "title": title[rid],
                "dimension": dim,
                "term": term,
                "label": other or labels.get(dim, {}).get(term, term),
                "role": role,
                "alt_confidence": conf,
                "main_confidence": main[key][0] if key in main else "",
                "in_missing": "yes" if (str(number[rid]), dim, term) in missing else "",
                "evidence": evidence,
                "record_id": rid,
            }
        )
    rows.sort(key=lambda x: (x["record_no"], order.get(x["dimension"], 99), x["term"]))

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "README"
    for line in README:
        ws.append([line])
    ws.column_dimensions["A"].width = 100
    columns = [
        "decision", "note", "record_no", "title", "dimension", "term", "label",
        "role", "alt_confidence", "main_confidence", "in_missing", "evidence",
        "record_id",
    ]  # fmt: skip
    _write_sheet(
        wb,
        "candidates",
        columns,
        rows,
        grey=set(columns) - {"decision", "note"},
        widths={"title": 30, "term": 30, "label": 30, "evidence": 60, "note": 30,
                "dimension": 18},
        validations={"decision": DECISIONS},
    )  # fmt: skip
    agree = agreement(con, main_run, alt_run)
    _write_sheet(
        wb,
        "agreement",
        list(agree[0]) if agree else ["dimension"],
        agree,
        grey=set(agree[0]) if agree else set(),
        widths={"dimension": 26},
        validations={},
    )
    record_cols = ["record_no", "record_id", "title", "input", "boris_url",
                   "abstract", "page_text_start"]  # fmt: skip
    _write_sheet(
        wb,
        "records",
        record_cols,
        [{c: r.get(c) for c in record_cols} for r in records],
        grey=set(record_cols),
        widths={"title": 40, "abstract": 70, "page_text_start": 70},
        validations={},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path, len(rows), dict(Counter(r["dimension"] for r in rows))
