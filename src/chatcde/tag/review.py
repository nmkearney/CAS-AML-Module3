"""A hand-check workbook for a tagging run (``chatcde evaluate review-sheet``).

Pilot runs are judged by reading records, not by eye on counts (decision 0028). This
writes an Excel workbook (decided in Excel, read by code: decision 0026) for a few
pilot records, drawn per input kind (full text, abstract only, title only;
``tagging.review.records``) with a fixed seed:

* **records:** one row per record: title, year, type, language, what the model saw
  (input kind), BORIS link, the abstract and the start of the page text.
* **tags:** one row per tag of the run on those records, with its code and label,
  role, confidence, whether it counts (>= 0.5), where the quote was found, and the
  quote. White columns are the reviewer's: ``decision`` (ok / wrong / unsure),
  ``correct_term`` (a code, for a wrong one) and ``note``.
* **missing:** empty rows for tags the model should have given (record, dimension,
  code, role).
* **codes:** every code with its label, per dimension, to look codes up.

The filled-in workbook gives a first precision per dimension and is the start of
the #16 reference set; scoring it is ``chatcde evaluate tagging`` (#16).

**Re-review after a rerun** (``--carry-from`` an earlier, filled-in workbook;
decision 0029): the same records, with the same numbers. A tag the earlier
workbook already judged (same record, dimension, code and role) keeps its
decision, with ``change = same``; only ``change = new`` rows need a look. A tag
judged under a code that has since been retired matches its replacement, but its
meaning may have changed, so the old decision goes into ``note`` and the row is
``new``. Tags of the earlier run that are gone are
listed in a ``dropped`` sheet with their old decision, and the earlier missing
tags are carried with ``now_tagged``.

"None" terms filled in by code (``tagging.none_terms``) are listed as tags too,
with the evidence "(filled in by code)", since they count like the others.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment

from chatcde.config import Config
from chatcde.schemas import ActorRole, Dimension
from chatcde.tag import tables
from chatcde.tag.answer import OTHER, codes_for
from chatcde.tag.curation import _write_sheet
from chatcde.tag.derive import derived_provenance, none_tags, read_sources, run_tags
from chatcde.tag.extract import build_texts
from chatcde.tag.regions import load_countries
from chatcde.tag.vocabulary import load_vocabulary
from chatcde.tools.vocabulary import dimension_entries

KINDS = ("fulltext", "abstract", "metadata")
DECISIONS = ["ok", "wrong", "unsure"]
MISSING_ROWS = 120
#: Columns of the ``dropped`` sheet (earlier tags the rerun no longer gives).
DROPPED_COLUMNS = [
    "decision", "correct_term", "note", "record_no", "title", "dimension", "term",
    "now_term", "label", "role", "confidence", "evidence",
]  # fmt: skip

README = [
    "Tagging review workbook (chatcde evaluate review-sheet; decision 0028).",
    "",
    "Read each record (sheet 'records'; follow the BORIS link for the full text),",
    "then judge its tags in sheet 'tags'. Edit only the white columns.",
    "",
    "decision: ok = the tag is right; wrong = it should not be there (put the right",
    "  code in correct_term if another term fits); unsure = cannot tell from the text.",
    "counted: whether the tag counts in the tools (confidence >= 0.5). Judge every",
    "  tag, counted or not: uncounted ones show what the caps removed.",
    "missing: add a row for each tag the model should have given (record_no,",
    "  dimension, code; role for actors). Codes are listed in sheet 'codes'.",
    "",
    "change (re-review only): same = judged in the earlier workbook (decision",
    "  carried); new = judge it. Sheet 'dropped' lists earlier tags the rerun no",
    "  longer gives, with their old decision; 'missing' marks now_tagged.",
    "",
    "input: what the model saw. fulltext = abstract and/or first pages;",
    "  abstract = abstract only; metadata = title, keywords and venue only.",
]


def kind_of(sources: str) -> str:
    """Input kind from a ``tag_state.sources`` value."""
    if "fulltext" in sources:
        return "fulltext"
    if "abstract" in sources:
        return "abstract"
    return "metadata"


def pick_records(
    con: duckdb.DuckDBPyConnection, cfg: Config, run: str
) -> list[tuple[str, str]]:
    """(record_id, input kind) for the review: per kind, a seeded draw from the pilot.

    Raises:
        RuntimeError: No pilot record has been tagged in ``run``.
    """
    tables.ensure(con, "tag_pilot", "tag_state")
    rows = con.execute(
        "SELECT DISTINCT s.record_id, s.sources FROM tag_state s "
        "JOIN tag_pilot p USING (record_id) WHERE s.run = ? AND s.status = 'ok' "
        "ORDER BY s.record_id",
        [run],
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no pilot records tagged in run '{run}'")
    by_kind: dict[str, list[str]] = {k: [] for k in KINDS}
    for rid, sources in rows:
        by_kind[kind_of(sources)].append(rid)
    rng = random.Random(cfg.tagging.review.seed)
    out: list[tuple[str, str]] = []
    for kind in KINDS:
        ids = sorted(set(by_kind[kind]))
        rng.shuffle(ids)
        out.extend(
            (rid, kind) for rid in sorted(ids[: cfg.tagging.review.records[kind]])
        )
    return out


def label_lookup(cfg: Config) -> dict[str, dict[str, str]]:
    """Dimension -> code -> label, for every taggable dimension (countries by name)."""
    vocab = load_vocabulary(cfg.vocab.file)
    countries = load_countries(cfg.tagging.m49_file)
    out: dict[str, dict[str, str]] = {}
    for dims in cfg.tagging.batches.values():
        for dim in dims:
            if dim == Dimension.COUNTRY.value:
                out[dim] = {c.alpha3: c.name for c in countries.values()}
                out[dim]["global_or_multi_country"] = "Global or multi-country"
                continue
            labels = {e.code: e.label for e in dimension_entries(vocab, Dimension(dim))}
            out[dim] = {c: labels.get(c, c) for c in codes_for(dim, vocab, countries)}
            if OTHER in out[dim]:
                out[dim][OTHER] = "Other (see label)"
    return out


def _sheet_rows(wb: Any, name: str) -> list[dict[str, Any]]:
    """Rows of one sheet of a review workbook as dicts (empty rows skipped)."""
    if name not in wb.sheetnames:
        return []
    it = wb[name].iter_rows(values_only=True)
    header = [str(h) if h is not None else "" for h in next(it, [])]
    rows = []
    for values in it:
        row = dict(zip(header, values, strict=False))
        if any(v not in (None, "") for v in row.values()):
            rows.append(row)
    return rows


def replacements(vocab: dict[str, Any]) -> dict[str, str]:
    """Retired code -> its replacement, over every curated dimension."""
    out: dict[str, str] = {}
    for node in vocab.values():
        if isinstance(node, dict):
            for item in node.get("retired") or []:
                if isinstance(item, dict) and item.get("replaced_by"):
                    out[str(item["code"])] = str(item["replaced_by"])
    return out


def _key(rid: Any, dim: Any, term: Any, role: Any) -> tuple[str, str, str, str]:
    """Matching key of a tag across workbooks (role empty for non-actors)."""
    return (str(rid), str(dim), str(term), str(role or ""))


def export_review(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    *,
    run: str | None = None,
    out: Path | None = None,
    force: bool = False,
    carry_from: Path | None = None,
) -> tuple[Path, int, int]:
    """Write the review workbook; return (path, records, tags).

    With ``carry_from`` (an earlier, filled-in workbook), the same records are
    reviewed and earlier decisions are carried (see module docstring).

    Raises:
        FileExistsError: The workbook exists and ``force`` is not set (it may hold
            a reviewer's decisions).
        RuntimeError: No pilot record has been tagged in the run.
    """
    run = run or cfg.tagging.run
    r = cfg.tagging.review
    path = out or r.dir / f"review_{run}.xlsx"
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; use --force to overwrite it")
    old_wb = load_workbook(carry_from, read_only=True) if carry_from else None
    if old_wb is not None:
        old_records = sorted(
            _sheet_rows(old_wb, "records"), key=lambda x: int(x["record_no"])
        )
        ids = [str(x["record_id"]) for x in old_records]
        kinds = {str(x["record_id"]): str(x["input"]) for x in old_records}
        number = {str(x["record_id"]): int(x["record_no"]) for x in old_records}
    else:
        picked = pick_records(con, cfg, run)
        ids = [rid for rid, _ in picked]
        kinds = dict(picked)
        number = {rid: n for n, rid in enumerate(ids, start=1)}
    texts = build_texts(
        con,
        ids,
        max_fulltext_chars=cfg.tagging.max_fulltext_chars,
        max_input_chars=cfg.tagging.max_input_chars,
    )
    meta = {
        row[0]: row[1:]
        for row in con.execute(
            "SELECT record_id, title, year, pub_type, languages, boris_url "
            "FROM records "
            "WHERE list_contains(?, record_id)",
            [ids],
        ).fetchall()
    }
    labels = label_lookup(cfg)
    order = {
        d: i for i, d in enumerate(d for b in cfg.tagging.batches.values() for d in b)
    }

    record_rows: list[dict[str, Any]] = []
    for rid in ids:
        title, year, pub_type, langs, url = meta[rid]
        t = texts.get(rid)
        record_rows.append(
            {
                "record_no": number[rid],
                "record_id": rid,
                "title": title,
                "year": year,
                "type": pub_type,
                "language": ", ".join(langs or []),
                "input": kinds[rid],
                "boris_url": url,
                "abstract": (t.abstract if t else "")[: r.text_chars],
                "page_text_start": (t.fulltext if t else "")[: r.text_chars],
            }
        )

    found = con.execute(
        "SELECT record_id, dimension, term, other_label, role, confidence, source, "
        "evidence FROM tag_runs WHERE run = ? AND list_contains(?, record_id)",
        [run, ids],
    ).fetchall()
    none_codes = cfg.tagging.none_terms
    if none_codes:  # "none" terms count like tags, so they are reviewed too
        vocab = load_vocabulary(cfg.vocab.file)
        llm = [
            t
            for t in run_tags(con, run)
            if t.record_id in number and t.term not in none_codes.values()
        ]
        found = [r for r in found if r[2] not in none_codes.values()]
        read = {
            d: {rid: src for rid, src in by.items() if rid in number}
            for d, by in read_sources(con, run, cfg.tagging.batches).items()
        }
        for t in none_tags(
            llm, read, none_codes, cfg.tagging.confidence["inferred"],
            derived_provenance(vocab),
        ):  # fmt: skip
            found.append(
                (t.record_id, t.dimension.value, t.term, None, None, t.confidence,
                 t.source.value, "(filled in by code)")
            )  # fmt: skip

    carried: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    dropped_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = [{} for _ in range(MISSING_ROWS)]
    if old_wb is not None:
        forward = replacements(load_vocabulary(cfg.vocab.file))
        for row in _sheet_rows(old_wb, "tags"):
            term = forward.get(str(row["term"]), str(row["term"]))
            carried[_key(row["record_id"], row["dimension"], term, row["role"])] = row
        now = {_key(r[0], r[1], r[2], r[4]) for r in found}
        for key, row in carried.items():
            if key not in now:
                dropped_rows.append(
                    {c: row.get(c) for c in DROPPED_COLUMNS if c != "now_term"}
                    | {"now_term": key[2] if key[2] != row["term"] else ""}
                )
        now_terms = {(str(number[r[0]]), r[1], str(r[2])) for r in found}
        missing_rows = []
        for row in _sheet_rows(old_wb, "missing"):
            key3 = (str(row.get("record_no")), str(row.get("dimension")),
                    str(row.get("term")))  # fmt: skip
            missing_rows.append(
                {**row, "now_tagged": "yes" if key3 in now_terms else "no"}
            )
        missing_rows += [{} for _ in range(MISSING_ROWS - len(missing_rows))]
        old_wb.close()

    tag_rows: list[dict[str, Any]] = []
    for rid, dim, term, other, role, conf, source, evidence in found:
        prev = carried.get(_key(rid, dim, term, role))
        note = (prev or {}).get("note") or ""
        if prev and str(prev["term"]) != str(term):  # judged under a retired code
            note = f"was {prev['term']}: {prev.get('decision') or ''} {note}".strip()
            prev = None
        tag_rows.append(
            {
                "decision": (prev or {}).get("decision") or "",
                "correct_term": (prev or {}).get("correct_term") or "",
                "note": note,
                **({"change": "same" if prev else "new"} if old_wb else {}),
                "record_no": number[rid],
                "title": (meta[rid][0] or "")[:80],
                "dimension": dim,
                "term": term,
                "label": other
                if term == OTHER
                else labels.get(dim, {}).get(term, term),
                "role": role or "",
                "confidence": conf,
                "counted": "yes" if conf >= 0.5 else "no",
                "source": source,
                "evidence": evidence or "",
                "record_id": rid,
            }
        )
    tag_rows.sort(
        key=lambda x: (x["record_no"], order.get(x["dimension"], 99), x["term"])
    )

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "README"
    for line in README:
        ws.append([line])
    ws.column_dimensions["A"].width = 100
    _write_sheet(
        wb,
        "records",
        list(record_rows[0]),
        record_rows,
        grey=set(record_rows[0]),
        widths={"title": 40, "abstract": 70, "page_text_start": 70, "boris_url": 30},
        validations={},
    )
    columns = [
        "decision", "correct_term", "note", "record_no", "title", "dimension", "term",
        "label", "role", "confidence", "counted", "source", "evidence", "record_id",
    ]  # fmt: skip
    if carry_from:
        columns.insert(3, "change")
    _write_sheet(
        wb,
        "tags",
        columns,
        tag_rows,
        grey=set(columns) - {"decision", "correct_term", "note"},
        widths={"title": 30, "term": 30, "label": 30, "evidence": 60, "note": 24,
                "correct_term": 24, "dimension": 18},
        validations={"decision": DECISIONS},
    )  # fmt: skip
    dims = sorted(order, key=order.get)  # type: ignore[arg-type]
    missing_cols = ["record_no", "dimension", "term", "role", "note"]
    if carry_from:
        missing_cols.append("now_tagged")
        dropped_rows.sort(key=lambda x: (int(x["record_no"]), str(x["dimension"])))
        _write_sheet(
            wb,
            "dropped",
            DROPPED_COLUMNS,
            dropped_rows,
            grey=set(DROPPED_COLUMNS),
            widths={"title": 30, "term": 30, "label": 30, "evidence": 60,
                    "note": 30, "dimension": 18},
            validations={},
        )  # fmt: skip
    _write_sheet(
        wb,
        "missing",
        missing_cols,
        missing_rows,
        grey=set(),
        widths={"dimension": 22, "term": 32, "note": 40},
        validations={
            "dimension": dims,
            "role": [x.value for x in ActorRole],
            "record_no": [str(n) for n in number.values()],
        },
    )
    code_rows = [
        {"dimension": d, "code": c, "label": lab}
        for d in dims
        for c, lab in labels[d].items()
    ]
    _write_sheet(
        wb,
        "codes",
        ["dimension", "code", "label"],
        code_rows,
        grey={"dimension", "code", "label"},
        widths={"dimension": 22, "code": 40, "label": 50},
        validations={},
    )
    for sheet in ("records",):
        for row in wb[sheet].iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path, len(ids), len(tag_rows)
