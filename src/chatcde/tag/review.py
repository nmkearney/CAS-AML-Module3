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
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import Workbook
from openpyxl.styles import Alignment

from chatcde.config import Config
from chatcde.schemas import ActorRole, Dimension
from chatcde.tag import tables
from chatcde.tag.answer import OTHER, codes_for
from chatcde.tag.curation import _write_sheet
from chatcde.tag.extract import build_texts
from chatcde.tag.regions import load_countries
from chatcde.tag.vocabulary import load_vocabulary
from chatcde.tools.vocabulary import dimension_entries

KINDS = ("fulltext", "abstract", "metadata")
DECISIONS = ["ok", "wrong", "unsure"]
MISSING_ROWS = 120

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


def export_review(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    *,
    run: str | None = None,
    out: Path | None = None,
    force: bool = False,
) -> tuple[Path, int, int]:
    """Write the review workbook; return (path, records, tags).

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

    tag_rows: list[dict[str, Any]] = []
    for rid, dim, term, other, role, conf, source, evidence in con.execute(
        "SELECT record_id, dimension, term, other_label, role, confidence, source, "
        "evidence FROM tag_runs WHERE run = ? AND list_contains(?, record_id)",
        [run, ids],
    ).fetchall():
        tag_rows.append(
            {
                "decision": "",
                "correct_term": "",
                "note": "",
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
    _write_sheet(
        wb,
        "missing",
        missing_cols,
        [{} for _ in range(MISSING_ROWS)],
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
