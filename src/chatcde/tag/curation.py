"""Curate the vocabulary in an Excel workbook, then apply it to the YAML (issue #14).

The developer decides in Excel; code writes the YAML (decision 0026). The flow
runs one way:

    vocab/candidates.yaml + draft_vocabulary.yaml
        --chatcde vocab review-->  vocab/curation/v<version>.xlsx   (decide here)
        --chatcde vocab apply--->  draft_vocabulary.yaml            (generated)

**Workbook.** One sheet per curated dimension, plus ``sub_area_scope`` and a
``README`` sheet. Each row is either a current term (``term:<label>``, decision
pre-set to ``keep``) or a candidate from open extraction (``cand:<id>``,
decision empty). Grey columns are evidence from the model and are never read
back; white columns are the developer's decisions.

**Decisions.**

* ``keep`` (current term): stays, with the code, label, definition and group in
  its row (changing the label renames it; the code stays).
* ``accept`` (candidate): becomes a new term.
* ``merge``: the row's label and example labels become ``includes`` of the term
  named in ``merge_into`` (any live code, including strategy sub-areas). A
  current term that is merged is retired with ``replaced_by``.
* ``merge_into`` on a ``keep`` or ``accept`` row lists **related** terms instead
  (codes separated by ``;``): the term stays and gets a ``related`` list. A
  label that names exactly one live term is read as its code, with a warning.
* ``retire`` (current term): removed, kept under ``retired`` without replacement.
* ``reject`` / ``defer`` (candidate): not added; ``defer`` marks it for later.

**Apply** checks every row first and refuses to write anything if there is an
error, listing all of them. It starts from the vocabulary the workbook was
exported from (kept in a hidden ``_base`` sheet), not from the current file.
It rewrites only the curated top-level blocks of the YAML (and ``sub_area``
for scope notes), leaving every other block and its comments untouched, and it
is deterministic: the same workbook gives the same YAML.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import BaseModel

from chatcde.tag.vocabulary import load_vocabulary

#: Curated dimensions (sheet name = YAML key) and their code prefixes.
PREFIXES: dict[str, str] = {
    "setting": "SET",
    "data_type": "DAT",
    "method": "MET",
    "system": "SYS",
    "actors": "ACT",
    "scale": "SCA",
    "justice_lens": "JUS",
    "additional_sub_area": "ASA",
}

#: Candidate kind in candidates.yaml -> sheet. Theme candidates are the evidence
#: for additional sub-areas (decision 0027).
KIND_SHEET: dict[str, str] = {
    "setting": "setting",
    "data_type": "data_type",
    "method": "method",
    "system": "system",
    "actors": "actors",
    "theme": "additional_sub_area",
}

#: Parent values for an additional sub-area.
ASA_PARENTS = ["JE", "SL", "SG", "TE", "cross_cutting", "outside"]

TERM_DECISIONS = ["keep", "merge", "retire"]
CANDIDATE_DECISIONS = ["accept", "merge", "reject", "defer"]
DECISIONS = ["keep", "accept", "merge", "retire", "reject", "defer"]

#: Columns the developer fills in (white), in sheet order.
EDIT_COLUMNS = [
    "decision",
    "code",
    "label",
    "definition",
    "group",
    "impact_area",
    "merge_into",
    "comment",
]
#: Evidence columns (grey); never read back except ``key``.
EVIDENCE_COLUMNS = [
    "key",
    "n_records",
    "suggestion",
    "model_label",
    "model_definition",
    "model_action",
    "model_existing_term",
    "model_dimension",
    "model_note",
    "example_labels",
]
COLUMNS = EDIT_COLUMNS + EVIDENCE_COLUMNS
SCOPE_COLUMNS = ["code", "label", "scope", "current_scope", "linked_themes"]

#: Hidden sheet holding the YAML the workbook was exported from.
BASE_SHEET = "_base"

#: Most strings kept in a term's ``includes`` list.
MAX_INCLUDES = 12

_GREY = PatternFill("solid", fgColor="E7E6E6")
_HEAD = PatternFill("solid", fgColor="BDD7EE")


class CurationError(Exception):
    """The workbook has problems; ``problems`` lists every one."""

    def __init__(self, problems: list[str]) -> None:
        """Keep the list of problems and show them all in the message."""
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s):\n- " + "\n- ".join(problems))


class ApplySummary(BaseModel):
    """What ``vocab apply`` changed, per dimension (also used for the CHANGELOG)."""

    terms: dict[str, int]  # live terms per dimension after applying
    kept: dict[str, int]
    renamed: dict[str, int]
    added: dict[str, int]
    merged: dict[str, int]  # rows merged into another term
    retired: dict[str, int]
    rejected: dict[str, int]
    deferred: dict[str, int]
    scope_notes_changed: int
    warnings: list[str]
    output: str
    code_map: str


# --- codes ---------------------------------------------------------------------


def code_slug(label: str, max_words: int = 5) -> str:
    """Kebab-case slug for a code: parentheses dropped, at most ``max_words`` words.

    ``"Futures methods (visioning, scenarios)" -> "futures-methods"``.
    """
    text = re.sub(r"\([^)]*\)", " ", label.lower())
    text = text.replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", text)
    return "-".join(words[:max_words]) or "term"


def suggest_code(dimension: str, label: str) -> str:
    """Suggested code for a term, e.g. ``MET-remote-sensing``."""
    return f"{PREFIXES[dimension]}-{code_slug(label)}"


def group_code(dimension: str, group: str) -> str:
    """Code of a group, e.g. ``MET-G-participatory-transdisciplinary``."""
    return f"{PREFIXES[dimension]}-G-{code_slug(group, 6)}"


def code_ok(dimension: str, code: str) -> bool:
    """Whether ``code`` is ``<PREFIX>-<kebab-slug>`` for its dimension."""
    return bool(re.fullmatch(rf"{PREFIXES[dimension]}-[a-z0-9]+(-[a-z0-9]+)*", code))


# --- reading the current vocabulary --------------------------------------------


@dataclass
class Term:
    """A term as read from the YAML (old or new layout)."""

    label: str
    code: str | None = None
    group: str | None = None
    definition: str | None = None
    includes: list[str] = field(default_factory=list)
    impact_area: str | None = None
    related: list[str] = field(default_factory=list)
    source: str | None = None
    note: str | None = None


def _humanise(key: str) -> str:
    """Group key to a label: ``"participatory_transdisciplinary"`` -> words."""
    return key.replace("_", " ").capitalize()


def current_terms(node: Any) -> list[Term]:
    """Terms of one curated dimension, from either YAML layout.

    The v0.2 layout has plain lists or ``group -> list`` mappings (``actors``
    under ``groups``); the layout written by :func:`apply_workbook` has
    ``groups`` and ``terms`` lists of mappings.
    """
    if node is None:
        return []
    if isinstance(node, dict) and "terms" in node:
        groups = {g["code"]: g["label"] for g in node.get("groups") or []}
        return [
            Term(
                label=t["label"],
                code=t.get("code"),
                group=groups.get(t.get("group"), t.get("group")),
                definition=t.get("definition"),
                includes=list(t.get("includes") or []),
                impact_area=t.get("impact_area"),
                related=list(t.get("related") or []),
                source=t.get("source"),
                note=t.get("note"),
            )
            for t in node["terms"]
        ]
    if isinstance(node, list):
        return [Term(label=str(x)) for x in node if isinstance(x, str)]
    if isinstance(node, dict):
        groups = node.get("groups", node)
        out: list[Term] = []
        for key, items in groups.items():
            if isinstance(items, list):
                out.extend(Term(label=str(x), group=_humanise(key)) for x in items)
        return out
    return []


# --- export ------------------------------------------------------------------


def _candidates(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Candidates per sheet, from candidates.yaml."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for kind, sheet in KIND_SHEET.items():
        for c in (doc.get(kind) or {}).get("candidates") or []:
            if "id" not in c:
                raise ValueError(f"{path}: candidates have no id; re-run propose")
            out[sheet].append(c)
    return out


def _suggestion(c: dict[str, Any], sheet: str) -> str:
    """The model's action as a decision hint."""
    action = c.get("action")
    if action == "merge_into_existing":
        return "merge"
    if sheet == "additional_sub_area" and action == "split_existing":
        return "accept, or fold into the sub-area's scope"
    return "accept" if action in ("new", "split_existing") else ""


def _term_code_lookup(
    vocab: dict[str, Any],
) -> dict[str, str]:
    """Existing term text (as the model saw it) -> suggested or existing code."""
    out: dict[str, str] = {}
    for dim in PREFIXES:
        for t in current_terms(vocab.get(dim)):
            out[t.label] = t.code or suggest_code(dim, t.label)
            if t.code:  # from v0.3 the model sees "code: label" (seed_terms)
                out[f"{t.code}: {t.label}"] = t.code
    for item in vocab.get("sub_area") or []:
        out[f"{item['code']}: {item['label']}"] = item["code"]
    return out


def build_rows(
    vocab: dict[str, Any], candidates: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    """Rows of every curated sheet: current terms first, then candidates by size."""
    lookup = _term_code_lookup(vocab)
    rows: dict[str, list[dict[str, Any]]] = {}
    for dim in PREFIXES:
        sheet_rows: list[dict[str, Any]] = []
        cands = candidates.get(dim, [])
        best_def: dict[str, str] = {}
        for c in cands:  # definitions for current terms from matching candidates
            ex = c.get("existing_term")
            if ex and c.get("action") == "merge_into_existing" and ex not in best_def:
                best_def[ex] = c.get("definition") or ""
        for t in current_terms(vocab.get(dim)):
            sheet_rows.append(
                {
                    "key": f"term:{t.label}",
                    "decision": "keep",
                    "code": t.code or suggest_code(dim, t.label),
                    "label": t.label,
                    "definition": t.definition or best_def.get(t.label, ""),
                    "group": t.group or "",
                    "impact_area": t.impact_area or "",
                    "merge_into": "; ".join(t.related),
                    "suggestion": "keep",
                }
            )
        for c in cands:
            existing = c.get("existing_term")
            prefill = c.get("action") == "merge_into_existing" or (
                dim == "additional_sub_area" and c.get("action") == "split_existing"
            )
            merge_into = lookup.get(existing, "") if existing and prefill else ""
            parent = ""
            if (
                dim == "additional_sub_area"
                and existing
                and existing[:2] in ASA_PARENTS
            ):
                parent = existing[:2]
            sheet_rows.append(
                {
                    "key": f"cand:{c['id']}",
                    "decision": "",
                    "code": suggest_code(dim, c.get("label") or "term"),
                    "label": c.get("label") or "",
                    "definition": c.get("definition") or "",
                    "group": "",
                    "impact_area": parent,
                    "merge_into": merge_into,
                    "n_records": c.get("n_records"),
                    "suggestion": _suggestion(c, dim),
                    "model_label": c.get("label"),
                    "model_definition": c.get("definition"),
                    "model_action": c.get("action"),
                    "model_existing_term": existing,
                    "model_dimension": c.get("suggested_dimension"),
                    "model_note": c.get("note"),
                    "example_labels": "; ".join(c.get("example_labels") or []),
                }
            )
        rows[dim] = sheet_rows
    return rows


def _scope_rows(
    vocab: dict[str, Any], candidates: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Rows of the sub_area_scope sheet, with the theme candidates linked to each."""
    linked: dict[str, list[str]] = defaultdict(list)
    for c in candidates.get("additional_sub_area", []):
        ex = c.get("existing_term") or ""
        if ":" in ex:
            linked[ex.split(":", 1)[0]].append(f"{c['label']} ({c['n_records']})")
    return [
        {
            "code": s["code"],
            "label": s["label"],
            "scope": s.get("scope", ""),
            "current_scope": s.get("scope", ""),
            "linked_themes": "; ".join(linked.get(s["code"], [])),
        }
        for s in vocab.get("sub_area") or []
    ]


README = [
    "Vocabulary curation workbook (chatcde vocab review / apply; decision 0026).",
    "",
    "Edit only the white columns. Grey columns are evidence and are ignored.",
    "When done (or any time), run: chatcde vocab apply  -> rewrites "
    "vocab/draft_vocabulary.yaml and lists any problems.",
    "",
    "decision, current terms (key term:...): keep | merge | retire",
    "decision, candidates (key cand:...): accept | merge | reject | defer",
    "  keep    - term stays; edit label/definition/group to change it (code stays)",
    "  accept  - candidate becomes a new term with the row's code/label/definition",
    "  merge   - row joins the term in merge_into (a code from any sheet, or a "
    "sub-area like SL-a); its labels become that term's 'includes'",
    "  merge_into on a keep/accept row = related terms (codes, ';'-separated); "
    "the term stays and links to them",
    "  retire  - current term is removed (kept under 'retired')",
    "  reject  - candidate is not added;  defer - not added now, revisit later",
    "",
    "code: <PREFIX>-<words-in-kebab-case>, e.g. MET-remote-sensing. Suggested; "
    "change it only before v0.3 is released. Codes are never reused.",
    "group: free text (e.g. 'Participatory and transdisciplinary'); at most two "
    "levels (group -> term).",
    "impact_area (additional_sub_area only): JE, SL, SG, TE, cross_cutting or outside.",
    "sub_area_scope: edit 'scope' to sharpen a strategy sub-area's scope note; "
    "linked_themes are theme candidates the model matched to it.",
    "",
    "Rules of thumb: accept or merge candidates with n_records >= 5; decide 3-4 "
    "case by case; reject country and place names; aim for 15-40 terms per sheet.",
    "n_records counts records in the 250-record sample (oversampled strata), not "
    "corpus shares.",
]


def _write_sheet(
    wb: Workbook,
    title: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    grey: set[str],
    widths: dict[str, int],
    validations: dict[str, list[str]],
) -> None:
    """One formatted sheet: header, rows, grey evidence, dropdowns, filters."""
    ws = wb.create_sheet(title)
    ws.append(columns)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEAD
    for row in rows:
        ws.append([row.get(c, "") if row.get(c) is not None else "" for c in columns])
    for idx, name in enumerate(columns, start=1):
        letter = ws.cell(row=1, column=idx).column_letter
        ws.column_dimensions[letter].width = widths.get(name, 14)
        for r in range(2, len(rows) + 2):
            cell = ws.cell(row=r, column=idx)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if name in grey:
                cell.fill = _GREY
        if name in validations and rows:
            dv = DataValidation(
                type="list",
                formula1='"' + ",".join(validations[name]) + '"',
                allow_blank=True,
            )
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{len(rows) + 1}")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions


_WIDTHS = {
    "decision": 10,
    "code": 30,
    "label": 30,
    "definition": 50,
    "group": 22,
    "impact_area": 12,
    "merge_into": 24,
    "comment": 24,
    "key": 18,
    "n_records": 8,
    "suggestion": 14,
    "model_label": 24,
    "model_definition": 40,
    "model_existing_term": 24,
    "model_note": 30,
    "example_labels": 50,
    "scope": 60,
    "current_scope": 50,
    "linked_themes": 50,
}


def export_workbook(
    vocab_path: Path,
    candidates_path: Path,
    out: Path,
    *,
    force: bool = False,
    carry_from: Path | None = None,
) -> dict[str, int]:
    """Write the curation workbook; returns rows per sheet.

    Args:
        vocab_path: Current vocabulary YAML.
        candidates_path: candidates.yaml from ``vocab extract`` (with ids).
        out: Workbook to write.
        force: Overwrite an existing workbook (its decisions are lost).
        carry_from: Earlier workbook whose decisions are copied to rows with
            the same sheet and key.

    Raises:
        FileExistsError: ``out`` exists and ``force`` is not set.
    """
    if out.exists() and not force:
        raise FileExistsError(f"{out} exists; use --force to overwrite its decisions")
    vocab = load_vocabulary(vocab_path)
    cands = _candidates(candidates_path)
    rows = build_rows(vocab, cands)
    scope = _scope_rows(vocab, cands)
    if carry_from is not None:
        old = read_workbook(carry_from)
        for sheet, sheet_rows in rows.items():
            previous = {r["key"]: r for r in old.get(sheet, [])}
            for r in sheet_rows:
                if r["key"] in previous:
                    r.update({c: previous[r["key"]].get(c, "") for c in EDIT_COLUMNS})
        old_scope = {r["code"]: r for r in old.get("sub_area_scope", [])}
        for r in scope:
            if r["code"] in old_scope:
                r["scope"] = old_scope[r["code"]].get("scope", r["scope"])
    wb = Workbook()
    readme = wb.active
    readme.title = "README"
    for line in README:
        readme.append([line])
    readme.column_dimensions["A"].width = 120
    for dim, sheet_rows in rows.items():
        validations = {"decision": DECISIONS}
        if dim == "additional_sub_area":
            validations["impact_area"] = ASA_PARENTS
        _write_sheet(
            wb,
            dim,
            COLUMNS,
            sheet_rows,
            grey=set(EVIDENCE_COLUMNS),
            widths=_WIDTHS,
            validations=validations,
        )
    _write_sheet(
        wb,
        "sub_area_scope",
        SCOPE_COLUMNS,
        scope,
        grey={"code", "label", "current_scope", "linked_themes"},
        widths=_WIDTHS,
        validations={},
    )
    base = wb.create_sheet(BASE_SHEET)
    for line in vocab_path.read_text(encoding="utf-8").splitlines():
        base.append([line])
    base.sheet_state = "hidden"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    counts = {dim: len(r) for dim, r in rows.items()}
    counts["sub_area_scope"] = len(scope)
    return counts


# --- reading the workbook -------------------------------------------------------


def _cell(value: Any) -> str:
    """A cell as clean text (Excel may return numbers or None)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def read_base(path: Path) -> dict[str, Any]:
    """The vocabulary the workbook was exported from (hidden sheet ``_base``).

    ``vocab apply`` builds the curated blocks from this base plus the decisions,
    never from the current YAML, so applying twice gives the same file.

    Raises:
        ValueError: The workbook has no base (made by an older version).
    """
    wb = load_workbook(path, read_only=True)
    try:
        if BASE_SHEET not in wb.sheetnames:
            raise ValueError(f"{path} has no {BASE_SHEET} sheet; export it again")
        lines = [
            "" if row[0] is None else str(row[0])
            for row in wb[BASE_SHEET].iter_rows(values_only=True)
        ]
    finally:
        wb.close()
    data = yaml.safe_load("\n".join(lines))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the {BASE_SHEET} sheet is not a vocabulary")
    return data


def read_workbook(path: Path) -> dict[str, list[dict[str, str]]]:
    """Rows of each known sheet as ``{column: text}``, header row by name."""
    wb = load_workbook(path, data_only=True, read_only=True)
    out: dict[str, list[dict[str, str]]] = {}
    for name in [*PREFIXES, "sub_area_scope"]:
        if name not in wb.sheetnames:
            continue
        it = wb[name].iter_rows(values_only=True)
        header = [_cell(h) for h in next(it, [])]
        rows = []
        for values in it:
            row = {h: _cell(v) for h, v in zip(header, values, strict=False) if h}
            if any(row.values()):
                rows.append(row)
        out[name] = rows
    wb.close()
    return out


# --- apply ---------------------------------------------------------------------


def _dims_block(
    terms: list[dict[str, Any]],
    groups: list[dict[str, str]],
    retired: list[dict[str, Any]],
    extra: dict[str, Any],
) -> dict[str, Any]:
    """The new YAML content of one curated dimension."""
    block: dict[str, Any] = dict(extra)
    if groups:
        block["groups"] = groups
    block["terms"] = terms
    if retired:
        block["retired"] = retired
    return block


def replace_blocks(text: str, blocks: dict[str, Any], insert_after: str) -> str:
    """Replace top-level YAML blocks in ``text``, keeping everything else verbatim.

    A block starts at a line ``key:`` in column 0 and runs to the next such line;
    comment and blank lines directly above the next key stay with that key.
    Keys not yet in the file are inserted after the block of ``insert_after``
    (or at the end if it is missing).
    """
    lines = text.splitlines(keepends=True)
    starts = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := re.match(r"([A-Za-z_]\w*):", line))
    ]

    def dump(key: str) -> str:
        return yaml.safe_dump(
            {key: blocks[key]}, sort_keys=False, allow_unicode=True, width=100
        )

    out: list[str] = lines[: starts[0][0]] if starts else list(lines)
    present = {k for _, k in starts}
    missing = [k for k in blocks if k not in present]
    for n, (i, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        body_end = end
        while body_end - 1 > i and (
            lines[body_end - 1].startswith("#") or not lines[body_end - 1].strip()
        ):
            body_end -= 1
        out.append(dump(key) if key in blocks else "".join(lines[i:body_end]))
        if key == insert_after:
            out.extend("\n" + dump(k) for k in missing)
            missing = []
        out.extend(lines[body_end:end])
    if missing:
        out.extend("\n" + dump(k) for k in missing)
    return "".join(out)


def merge_names(existing: list[str], new: list[str], *, exclude: str = "") -> list[str]:
    """Append ``new`` names to ``existing``, skipping blanks and repeats (any case).

    ``exclude`` (the term's own label) is never listed as one of its includes.
    """
    out = list(existing)
    seen = {x.lower() for x in out} | {exclude.lower()}
    for name in new:
        if name and name.lower() not in seen:
            out.append(name)
            seen.add(name.lower())
    return out


def resolve_target(
    value: str,
    all_live: set[str],
    label_index: dict[str, list[str]],
    where: str,
    problems: list[str],
    warnings: list[str],
) -> str | None:
    """The code a ``merge_into`` entry points to, or None (with a problem recorded).

    A live code or sub-area code is taken as it is. A label is accepted when it
    matches exactly one live term or sub-area (any sheet, any case); it is turned
    into that code and a warning says so, so the workbook can be corrected later.
    """
    if value in all_live:
        return value
    codes = label_index.get(value.strip().lower(), [])
    if len(codes) == 1:
        warnings.append(f"{where}: '{value}' read as the label of {codes[0]}")
        return codes[0]
    if codes:
        problems.append(f"{where}: '{value}' matches several terms: {', '.join(codes)}")
    else:
        problems.append(f"{where}: merge_into '{value}' is not a live code")
    return None


def apply_workbook(
    workbook: Path,
    vocab_path: Path,
    *,
    version: str,
    terms_range: tuple[int, int],
    code_map_path: Path,
    dry_run: bool = False,
) -> ApplySummary:
    """Check the workbook and write the curated vocabulary (see module docstring).

    Args:
        workbook: The curation workbook.
        vocab_path: Vocabulary YAML to rewrite.
        version: Version to write at the top of the YAML (``vocab.version``).
        terms_range: (min, max) live terms per dimension that has candidates
            (scale and justice_lens are short fixed lists); outside is a warning.
        code_map_path: CSV of old label -> new code (for remapping tags).
        dry_run: Check and summarise only; write nothing.

    Raises:
        CurationError: Any row is invalid; nothing is written.
    """
    sheets = read_workbook(workbook)
    vocab = read_base(workbook)  # the vocabulary the workbook was made from
    problems: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Counter[str]] = defaultdict(Counter)

    # Pass 1: live codes (keep/accept) per dimension, and validation per row.
    live: dict[str, dict[str, dict[str, Any]]] = {}
    seen_codes: dict[str, str] = {}
    sub_area_codes = {s["code"]: s for s in vocab.get("sub_area") or []}
    sub_area_labels = {s["label"].lower() for s in vocab.get("sub_area") or []}
    old_terms = {
        dim: {t.label: t for t in current_terms(vocab.get(dim))} for dim in PREFIXES
    }
    for dim in PREFIXES:
        live[dim] = {}
        for n, row in enumerate(sheets.get(dim, []), start=2):
            where = f"{dim} row {n}"
            key, decision = row.get("key", ""), row.get("decision", "").lower()
            is_term = key.startswith("term:")
            allowed = TERM_DECISIONS if is_term else CANDIDATE_DECISIONS
            if not decision:
                problems.append(f"{where}: no decision")
                continue
            if decision not in allowed:
                problems.append(
                    f"{where}: '{decision}' not allowed here (use {', '.join(allowed)})"
                )
                continue
            stats[dim][decision] += 1
            if decision in ("keep", "accept"):
                code, label = row.get("code", ""), row.get("label", "")
                if not code or not label:
                    problems.append(f"{where}: {decision} needs a code and a label")
                    continue
                if not code_ok(dim, code):
                    problems.append(
                        f"{where}: code '{code}' must look like "
                        f"{PREFIXES[dim]}-words-in-kebab-case"
                    )
                    continue
                if code in seen_codes:
                    problems.append(
                        f"{where}: code '{code}' already used ({seen_codes[code]})"
                    )
                    continue
                seen_codes[code] = where
                if dim == "additional_sub_area":
                    if row.get("impact_area", "") not in ASA_PARENTS:
                        problems.append(
                            f"{where}: impact_area must be one of "
                            + ", ".join(ASA_PARENTS)
                        )
                    if label.lower() in sub_area_labels:
                        problems.append(
                            f"{where}: '{label}' repeats a strategy sub-area"
                        )
                if not row.get("definition"):
                    warnings.append(f"{where}: '{label}' has no definition")
                live[dim][code] = {"row": row, "includes": [], "related": []}
    all_live = {c for d in live.values() for c in d} | set(sub_area_codes)
    label_index: dict[str, list[str]] = defaultdict(list)
    for d in live.values():
        for c, item in d.items():
            label_index[item["row"]["label"].strip().lower()].append(c)
    for c, s in sub_area_codes.items():
        label_index[str(s["label"]).strip().lower()].append(c)

    def target(value: str, where: str) -> str | None:
        return resolve_target(value, all_live, label_index, where, problems, warnings)

    # Pass 2: merges and retirements.
    sub_area_includes: dict[str, list[str]] = defaultdict(list)
    retired: dict[str, list[dict[str, str]]] = defaultdict(list)
    code_map: list[tuple[str, str, str, str]] = []  # dimension, old label, code, how
    for dim in PREFIXES:
        for n, row in enumerate(sheets.get(dim, []), start=2):
            where = f"{dim} row {n}"
            decision = row.get("decision", "").lower()
            key = row.get("key", "")
            label = row.get("label", "")
            if decision in ("keep", "accept") and row.get("merge_into"):
                # merge_into on a live term = related terms (0026), ';'-separated.
                own = row.get("code", "")
                if own not in live[dim]:
                    continue  # the row already failed in pass 1
                for value in row["merge_into"].split(";"):
                    if not value.strip():
                        continue
                    code = target(value.strip(), where)
                    if code == own:
                        problems.append(f"{where}: a term cannot be related to itself")
                    elif code and code not in live[dim][own]["related"]:
                        live[dim][own]["related"].append(code)
                continue
            if decision == "merge":
                resolved = target(row.get("merge_into", ""), where)
                if resolved is None:
                    continue
                target_code = resolved
                names = [label, row.get("model_label", "")]
                names += [
                    re.sub(r"\s*\(\d+\)$", "", x).strip()
                    for x in row.get("example_labels", "").split(";")
                ]
                bucket = (
                    sub_area_includes[target_code]
                    if target_code in sub_area_codes
                    else next(
                        live[d][target_code]["includes"]
                        for d in live
                        if target_code in live[d]
                    )
                )
                bucket[:] = merge_names(bucket, names)
                if key.startswith("term:"):
                    old_label = key.removeprefix("term:")
                    retired[dim].append(
                        {
                            "code": row.get("code") or suggest_code(dim, old_label),
                            "label": old_label,
                            "replaced_by": target_code,
                        }
                    )
                    code_map.append((dim, old_label, target_code, "merged"))
            elif decision == "retire":
                old_label = key.removeprefix("term:")
                retired[dim].append(
                    {
                        "code": row.get("code") or suggest_code(dim, old_label),
                        "label": old_label,
                        "replaced_by": None,
                    }  # type: ignore[dict-item]
                )
                code_map.append((dim, old_label, "", "retired"))
            elif decision == "keep":
                old_label = key.removeprefix("term:")
                code_map.append((dim, old_label, row["code"], "kept"))
                if old_label != label:
                    stats[dim]["renamed"] += 1
    for dim, items in retired.items():
        for item in items:
            if item["code"] in all_live:
                problems.append(
                    f"{dim}: retired code '{item['code']}' is also a live code"
                )
    if problems:
        raise CurationError(problems)

    # Build the blocks.
    blocks: dict[str, Any] = {}
    summary_terms: dict[str, int] = {}
    for dim in PREFIXES:
        group_labels: dict[str, str] = {}
        terms: list[dict[str, Any]] = []
        for code, item in live[dim].items():
            row = item["row"]
            key = row.get("key", "")
            old = (
                old_terms[dim].get(key.removeprefix("term:"))
                if key.startswith("term:")
                else None
            )
            term: dict[str, Any] = {"code": code, "label": row["label"]}
            if row.get("group"):
                gcode = group_code(dim, row["group"])
                group_labels.setdefault(gcode, row["group"])
                term["group"] = gcode
            if dim == "additional_sub_area":
                term["impact_area"] = row["impact_area"]
            term["definition"] = row.get("definition") or None
            includes = merge_names(
                old.includes if old else [], item["includes"], exclude=row["label"]
            )
            if includes:
                term["includes"] = includes[:MAX_INCLUDES]
            if item["related"]:
                term["related"] = item["related"]
            term["source"] = (
                (old.source or "seed") if key.startswith("term:") else "candidate"
            )
            if row.get("comment"):
                term["note"] = row["comment"]
            terms.append(term)
        terms.sort(key=lambda t: (t.get("group") or "", t["label"].lower()))
        groups = [{"code": c, "label": lab} for c, lab in sorted(group_labels.items())]
        extra = {}
        node = vocab.get(dim)
        if dim == "actors" and isinstance(node, dict) and node.get("roles"):
            extra["roles"] = node["roles"]
        old_retired = node.get("retired", []) if isinstance(node, dict) else []
        blocks[dim] = _dims_block(
            terms, groups, list(old_retired) + retired[dim], extra
        )
        summary_terms[dim] = len(terms)
        lo, hi = terms_range
        has_candidates = dim in KIND_SHEET.values()
        if has_candidates and terms and not lo <= len(terms) <= hi:
            warnings.append(f"{dim}: {len(terms)} terms, outside the target {lo}-{hi}")

    # Scope notes and includes for strategy sub-areas.
    scope_rows = {r.get("code"): r for r in sheets.get("sub_area_scope", [])}
    changed = 0
    sub_area: list[dict[str, Any]] = []
    for s in vocab.get("sub_area") or []:
        item = dict(s)
        new_scope = scope_rows.get(s["code"], {}).get("scope")
        if new_scope and new_scope != s.get("scope"):
            item["scope"] = new_scope
            changed += 1
        incl = merge_names(
            s.get("includes") or [],
            sub_area_includes.get(s["code"], []),
            exclude=s["label"],
        )
        if incl:
            item["includes"] = incl[:MAX_INCLUDES]
        sub_area.append(item)
    blocks["sub_area"] = sub_area

    text = vocab_path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^version: .*$", f"version: {version}", text, count=1)
    new_text = replace_blocks(text, blocks, insert_after="sub_area")
    if not dry_run:
        vocab_path.write_text(new_text, encoding="utf-8")
        code_map_path.parent.mkdir(parents=True, exist_ok=True)
        with code_map_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["dimension", "old_label", "code", "how"])
            w.writerows(sorted(code_map))

    def per(name: str) -> dict[str, int]:
        return {d: stats[d][name] for d in PREFIXES if stats[d][name]}

    return ApplySummary(
        terms=summary_terms,
        kept=per("keep"),
        renamed=per("renamed"),
        added=per("accept"),
        merged=per("merge"),
        retired=per("retire"),
        rejected=per("reject"),
        deferred=per("defer"),
        scope_notes_changed=changed,
        warnings=warnings,
        output=str(vocab_path),
        code_map=str(code_map_path),
    )
