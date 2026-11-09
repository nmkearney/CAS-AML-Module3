"""Tests for the curation workbook: `chatcde vocab review` and `vocab apply` (#14)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from openpyxl import load_workbook
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.tag.curation import (
    CurationError,
    apply_workbook,
    code_slug,
    current_terms,
    export_workbook,
    merge_names,
    read_workbook,
    replace_blocks,
    suggest_code,
)
from chatcde.tag.vocabulary import load_vocabulary, seed_terms

VOCAB = """\
# Test vocabulary
version: 0.2

impact_area:
  - {code: SL, label: Sustainable land systems, boris_unit: "Sustainable Land Systems"}

sub_area:
  - code: SL-a
    label: Sustainable land use
    scope: Cropland, grazing land, forests.

method:  # proposed seed list
  qualitative:
    - Case study
    - Interviews and focus groups
  synthesis:
    - Literature and systematic review

country: {standard: ISO 3166-1 alpha-3}  # untouched block

pathway:  # tag only when explicit
  - {code: P1, label: Informed decisions, rule: "Evidence for decisions"}

research_mode:
  - {code: M1, label: Transformative research, definition: "Co-creates solutions"}
  - {code: M2, label: Transformation research, definition: "Analyses change"}

setting:  # proposed
  - Mountains
  - Urban

scale:
  - National

actors:
  roles: [studied, co_producer, audience]
  groups:
    land_users: [Pastoralists]
"""

CANDIDATES = {
    "generated": {"at": "x"},
    "method": {
        "candidates": [
            {
                "id": "aaaaaaaaaa",
                "label": "Case studies",
                "definition": "In-depth study of cases.",
                "action": "merge_into_existing",
                "existing_term": "Case study",
                "suggested_dimension": "method",
                "note": None,
                "n_records": 12,
                "example_labels": ["case study analysis (8)", "case studies (4)"],
            },
            {
                "id": "bbbbbbbbbb",
                "label": "Transdisciplinary research",
                "definition": "Research with non-academic partners.",
                "action": "new",
                "existing_term": None,
                "suggested_dimension": "method",
                "note": None,
                "n_records": 9,
                "example_labels": ["transdisciplinary research (9)"],
            },
        ]
    },
    "setting": {
        "candidates": [
            {
                "id": "cccccccccc",
                "label": "Switzerland",
                "definition": "The country.",
                "action": "new",
                "existing_term": None,
                "suggested_dimension": "setting",
                "note": "Labels name a specific country.",
                "n_records": 20,
                "example_labels": ["switzerland (12)"],
            }
        ]
    },
    "theme": {
        "candidates": [
            {
                "id": "dddddddddd",
                "label": "Food security",
                "definition": "Access to sufficient food.",
                "action": "new",
                "existing_term": None,
                "suggested_dimension": "sub_area",
                "note": None,
                "n_records": 14,
                "example_labels": ["food security (11)"],
            },
            {
                "id": "eeeeeeeeee",
                "label": "Sustainable land management",
                "definition": "Practices that keep land productive.",
                "action": "split_existing",
                "existing_term": "SL-a: Sustainable land use",
                "suggested_dimension": "sub_area",
                "note": None,
                "n_records": 24,
                "example_labels": ["sustainable land management (20)", "SLM (2)"],
            },
        ]
    },
}

#: Decisions for a valid workbook: key -> column values.
DECISIONS: dict[str, dict[str, str]] = {
    "cand:aaaaaaaaaa": {"decision": "merge"},  # merge_into pre-filled
    "cand:bbbbbbbbbb": {"decision": "accept", "group": "Participatory"},
    "cand:cccccccccc": {"decision": "reject"},
    "cand:dddddddddd": {"decision": "accept", "impact_area": "cross_cutting"},
    "cand:eeeeeeeeee": {"decision": "merge"},  # into SL-a, pre-filled
    "term:Interviews and focus groups": {
        "decision": "keep",
        "label": "Interviews and focus group discussions",
        "definition": "Talking with people.",
    },
    "term:Literature and systematic review": {
        "decision": "merge",
        "merge_into": "MET-case-study",
    },
    "term:Urban": {"decision": "retire"},
}


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    vocab = tmp_path / "vocab.yaml"
    vocab.write_text(VOCAB, encoding="utf-8")
    cands = tmp_path / "candidates.yaml"
    cands.write_text(yaml.safe_dump(CANDIDATES), encoding="utf-8")
    return {
        "vocab": vocab,
        "cands": cands,
        "wb": tmp_path / "curation" / "v0.3.xlsx",
        "map": tmp_path / "curation" / "v0.3_codes.csv",
    }


def fill(
    path: Path, decisions: dict[str, dict[str, str]], sheet_scope: str = ""
) -> None:
    """Write values into the workbook rows with the given keys (and a scope note)."""
    wb = load_workbook(path)
    for ws in wb.worksheets:
        header = [c.value for c in ws[1]]
        if "key" in header:
            col = {n: i for i, n in enumerate(header)}
            for row in ws.iter_rows(min_row=2):
                values = decisions.get(row[col["key"]].value)
                for name, value in (values or {}).items():
                    row[col[name]].value = value
        elif ws.title == "sub_area_scope" and sheet_scope:
            ws.cell(row=2, column=header.index("scope") + 1).value = sheet_scope
    wb.save(path)


def apply(files: dict[str, Path], **kw: object):
    return apply_workbook(
        files["wb"],
        files["vocab"],
        version="0.3",
        terms_range=(1, 40),
        code_map_path=files["map"],
        **kw,
    )


def test_codes():
    assert code_slug("Futures methods (visioning, scenarios)") == "futures-methods"
    assert code_slug("GIS & spatial analysis") == "gis-and-spatial-analysis"
    assert suggest_code("method", "Remote sensing") == "MET-remote-sensing"
    assert suggest_code("additional_sub_area", "Food security") == "ASA-food-security"


def test_merge_names_dedupes_case_insensitively():
    assert merge_names(["A b"], ["a B", "", "c", "Own"], exclude="own") == ["A b", "c"]


def test_current_terms_reads_old_layouts():
    terms = current_terms({"qualitative": ["Case study"], "synthesis": ["Review"]})
    assert [(t.label, t.group) for t in terms] == [
        ("Case study", "Qualitative"),
        ("Review", "Synthesis"),
    ]
    assert [t.label for t in current_terms(["A", "B"])] == ["A", "B"]
    assert [t.label for t in current_terms({"roles": [], "groups": {"g": ["P"]}})] == [
        "P"
    ]


def test_export_workbook(files: dict[str, Path]):
    counts = export_workbook(files["vocab"], files["cands"], files["wb"])
    assert counts["method"] == 5 and counts["additional_sub_area"] == 2
    assert counts["sub_area_scope"] == 1
    rows = read_workbook(files["wb"])
    method = {r["key"]: r for r in rows["method"]}
    term = method["term:Case study"]
    assert term["decision"] == "keep" and term["code"] == "MET-case-study"
    assert term["definition"] == "In-depth study of cases."  # from the merge candidate
    assert term["group"] == "Qualitative"
    cand = method["cand:aaaaaaaaaa"]
    assert cand["decision"] == "" and cand["merge_into"] == "MET-case-study"
    assert cand["suggestion"] == "merge" and cand["n_records"] == "12"
    assert method["cand:bbbbbbbbbb"]["merge_into"] == ""
    asa = {r["key"]: r for r in rows["additional_sub_area"]}
    assert asa["cand:eeeeeeeeee"]["merge_into"] == "SL-a"
    assert asa["cand:eeeeeeeeee"]["impact_area"] == "SL"
    scope = rows["sub_area_scope"][0]
    assert scope["linked_themes"] == "Sustainable land management (24)"
    with pytest.raises(FileExistsError):
        export_workbook(files["vocab"], files["cands"], files["wb"])

    wb = load_workbook(files["wb"])
    assert wb.sheetnames[0] == "README"
    assert wb["method"].data_validations.dataValidation  # decision dropdown


def test_export_carries_decisions_forward(files: dict[str, Path], tmp_path: Path):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS, sheet_scope="New scope.")
    newer = tmp_path / "v0.4.xlsx"
    export_workbook(files["vocab"], files["cands"], newer, carry_from=files["wb"])
    rows = read_workbook(newer)
    asa = {r["key"]: r for r in rows["additional_sub_area"]}
    assert asa["cand:dddddddddd"]["decision"] == "accept"
    assert asa["cand:dddddddddd"]["impact_area"] == "cross_cutting"
    assert rows["sub_area_scope"][0]["scope"] == "New scope."


def test_apply_writes_curated_yaml(files: dict[str, Path]):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS, sheet_scope="Cropland, grazing land, forests, SLM.")
    s = apply(files)
    assert s.added == {"method": 1, "additional_sub_area": 1}
    assert s.retired == {"setting": 1} and s.renamed == {"method": 1}
    assert s.scope_notes_changed == 1

    text = files["vocab"].read_text()
    assert "country: {standard: ISO 3166-1 alpha-3}  # untouched block" in text
    assert text.index("additional_sub_area:") < text.index("method:")
    vocab = load_vocabulary(files["vocab"])
    assert vocab["version"] == 0.3

    method = vocab["method"]
    terms = {t["code"]: t for t in method["terms"]}
    assert set(terms) == {
        "MET-case-study",
        "MET-interviews-and-focus-groups",
        "MET-transdisciplinary-research",
    }
    case = terms["MET-case-study"]
    assert case["source"] == "seed" and case["group"] == "MET-G-qualitative"
    assert "Case studies" in case["includes"]
    assert "case study analysis" in case["includes"]
    assert "Literature and systematic review" in case["includes"]  # merged term
    interviews = terms["MET-interviews-and-focus-groups"]
    assert interviews["label"] == "Interviews and focus group discussions"  # renamed
    assert terms["MET-transdisciplinary-research"]["source"] == "candidate"
    assert method["retired"] == [
        {
            "code": "MET-literature-and-systematic-review",
            "label": "Literature and systematic review",
            "replaced_by": "MET-case-study",
        }
    ]
    assert {"code": "MET-G-participatory", "label": "Participatory"} in method["groups"]

    asa = vocab["additional_sub_area"]["terms"]
    assert asa == [
        {
            "code": "ASA-food-security",
            "label": "Food security",
            "impact_area": "cross_cutting",
            "definition": "Access to sufficient food.",
            "source": "candidate",
        }
    ]
    sl_a = vocab["sub_area"][0]
    assert sl_a["scope"].endswith("SLM.")
    assert "Sustainable land management" in sl_a["includes"]
    assert vocab["setting"]["retired"][0]["code"] == "SET-urban"
    assert vocab["actors"]["roles"] == ["studied", "co_producer", "audience"]
    assert seed_terms(vocab, "method") == [  # sorted by group, then label
        "MET-transdisciplinary-research: Transdisciplinary research",
        "MET-case-study: Case study",
        "MET-interviews-and-focus-groups: Interviews and focus group discussions",
    ]

    code_map = files["map"].read_text().splitlines()
    assert "method,Literature and systematic review,MET-case-study,merged" in code_map
    assert "setting,Urban,,retired" in code_map

    first = files["vocab"].read_text()
    apply(files)
    assert files["vocab"].read_text() == first  # deterministic, built from _base

    # A change to a block that is not curated (e.g. from another PR) survives.
    edited = first.replace("# untouched block", "# edited elsewhere")
    files["vocab"].write_text(edited)
    apply(files)
    assert files["vocab"].read_text() == edited


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"cand:bbbbbbbbbb": {"decision": ""}}, "no decision"),
        ({"cand:bbbbbbbbbb": {"decision": "keep"}}, "not allowed"),
        ({"term:Mountains": {"decision": "accept"}}, "not allowed"),
        ({"cand:bbbbbbbbbb": {"code": "XYZ-thing"}}, "must look like MET-"),
        ({"cand:bbbbbbbbbb": {"code": "MET-case-study"}}, "already used"),
        ({"cand:aaaaaaaaaa": {"merge_into": "MET-nothing"}}, "not a live code"),
        ({"cand:dddddddddd": {"impact_area": ""}}, "impact_area must be"),
        ({"cand:dddddddddd": {"label": "Sustainable land use"}}, "repeats a strategy"),
        ({"cand:bbbbbbbbbb": {"label": ""}}, "needs a code and a label"),
    ],
)
def test_apply_refuses_invalid_rows(
    files: dict[str, Path], change: dict[str, dict[str, str]], message: str
):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS)
    fill(files["wb"], change)
    before = files["vocab"].read_text()
    with pytest.raises(CurationError, match=message):
        apply(files)
    assert files["vocab"].read_text() == before and not files["map"].exists()


def test_apply_writes_related_links_and_reads_labels(files: dict[str, Path]):
    """merge_into on an accepted row = related terms; a unique label means its code."""
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS)
    fill(
        files["wb"],
        {
            "cand:dddddddddd": {"merge_into": "SL-a; Case study"},
            "cand:aaaaaaaaaa": {"merge_into": "case study"},  # a label, not a code
        },
    )
    s = apply(files)
    note = "'Case study' read as the label of MET-case-study"
    assert any(note in w for w in s.warnings)
    vocab = load_vocabulary(files["vocab"])
    food = vocab["additional_sub_area"]["terms"][0]
    assert food["related"] == ["SL-a", "MET-case-study"]
    case = next(t for t in vocab["method"]["terms"] if t["code"] == "MET-case-study")
    assert "case study analysis" in case["includes"]  # the merge still happened

    # Related links survive a re-export (kept terms show them in merge_into).
    newer = files["wb"].with_name("v0.4.xlsx")
    export_workbook(files["vocab"], files["cands"], newer)
    asa = {r["key"]: r for r in read_workbook(newer)["additional_sub_area"]}
    assert asa["term:Food security"]["merge_into"] == "SL-a; MET-case-study"


@pytest.mark.parametrize(
    ("merge_into", "message"),
    [
        ("MET-nothing", "not a live code"),
        ("ASA-food-security", "cannot be related to itself"),
    ],
)
def test_apply_refuses_bad_related_links(
    files: dict[str, Path], merge_into: str, message: str
):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS)
    fill(files["wb"], {"cand:dddddddddd": {"merge_into": merge_into}})
    with pytest.raises(CurationError, match=message):
        apply(files)


def test_apply_lists_every_problem_and_warns(files: dict[str, Path]):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    with pytest.raises(CurationError) as exc:
        apply(files)
    assert len(exc.value.problems) == 5  # the five candidates have no decision

    fill(files["wb"], DECISIONS)
    s = apply_workbook(
        files["wb"],
        files["vocab"],
        version="0.3",
        terms_range=(15, 40),
        code_map_path=files["map"],
        dry_run=True,
    )
    assert any("no definition" in w for w in s.warnings)  # Mountains, Case study...
    assert any("method: 3 terms, outside the target 15-40" in w for w in s.warnings)
    assert not any(w.startswith("scale:") for w in s.warnings)  # no candidates
    assert "version: 0.2" in files["vocab"].read_text()  # dry run wrote nothing


def test_replace_blocks_keeps_comments_and_inserts():
    text = "# head\nversion: 1\n\na:  # note\n  - x\n\n# about b\nb: 2\n"
    out = replace_blocks(text, {"a": ["y"], "c": 3}, insert_after="a")
    assert out == "# head\nversion: 1\n\na:\n- y\n\nc: 3\n\n# about b\nb: 2\n"
    assert replace_blocks(text, {"z": 1}, insert_after="nope").endswith(
        "b: 2\n\nz: 1\n"
    )


def test_cli_review_and_apply(files: dict[str, Path], tmp_path: Path):
    override = tmp_path / "o.yaml"
    override.write_text(
        yaml.safe_dump(
            {
                "logging": {"file": None},
                "vocab": {
                    "file": str(files["vocab"]),
                    "version": "0.3",
                    "curation_dir": str(files["wb"].parent),
                    "terms_per_dimension": [1, 40],
                },
                "vocab_extract": {"output": str(files["cands"])},
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    r = runner.invoke(app, ["--config", str(override), "vocab", "apply"])
    assert r.exit_code == 1 and "vocab review" in r.output
    r = runner.invoke(app, ["--config", str(override), "vocab", "review"])
    assert r.exit_code == 0, r.output
    assert files["wb"].is_file()
    r = runner.invoke(app, ["--config", str(override), "vocab", "apply"])
    assert (
        r.exit_code == 1
        and "no decision" in r.output
        and "Nothing was written" in r.output
    )
    fill(files["wb"], DECISIONS)
    r = runner.invoke(app, ["--config", str(override), "vocab", "apply", "--dry-run"])
    assert r.exit_code == 0 and "dry run" in r.output
    r = runner.invoke(app, ["--config", str(override), "vocab", "apply"])
    assert r.exit_code == 0, r.output
    assert "additional_sub_area" in files["vocab"].read_text()
    r = runner.invoke(app, ["--config", str(override), "vocab", "review"])
    assert r.exit_code == 1 and "--force" in r.output


def _append_row(path: Path, sheet: str, values: dict[str, str]) -> None:
    """Add a row at the bottom of a sheet, as a curator would."""
    wb = load_workbook(path)
    ws = wb[sheet]
    header = [c.value for c in ws[1]]
    ws.append([values.get(h, None) for h in header])
    wb.save(path)


def test_includes_column_replaces_synonyms_and_curator_rows(files: dict[str, Path]):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS)
    fill(files["wb"], {"term:Pastoralists": {"includes": "herders; ; nomads"}})
    _append_row(
        files["wb"],
        "method",
        {
            "decision": "accept",
            "code": "MET-sound-recording",
            "label": "Sound recording",
            "definition": "Recording sounds as data.",
            "includes": "audio recording",
        },
    )
    s = apply(files)
    assert s.added["method"] == 2
    vocab = yaml.safe_load(files["vocab"].read_text(encoding="utf-8"))
    actors = {t["code"]: t for t in vocab["actors"]["terms"]}
    assert actors["ACT-pastoralists"]["includes"] == ["herders", "nomads"]
    method = {t["code"]: t for t in vocab["method"]["terms"]}
    assert method["MET-sound-recording"]["source"] == "curator"
    assert method["MET-sound-recording"]["includes"] == ["audio recording"]
    # merged labels are still added on top of the edited list
    assert "Case studies" in method["MET-case-study"]["includes"]


def test_strategy_terms_sheet_edits_labels_and_definitions(files: dict[str, Path]):
    counts = export_workbook(files["vocab"], files["cands"], files["wb"])
    assert counts["strategy_terms"] == 3
    rows = {r["code"]: r for r in read_workbook(files["wb"])["strategy_terms"]}
    assert rows["P1"]["definition"] == "Evidence for decisions"  # pathway: rule
    fill(files["wb"], DECISIONS)
    wb = load_workbook(files["wb"])
    ws = wb["strategy_terms"]
    header = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[header.index("code")].value == "M2":
            row[header.index("label")].value = "Research on transformations"
            row[header.index("definition")].value = "Analyses societal change."
    wb.save(files["wb"])
    s = apply(files)
    assert s.strategy_terms_changed == 1
    text = files["vocab"].read_text(encoding="utf-8")
    vocab = yaml.safe_load(text)
    assert vocab["research_mode"][1] == {
        "code": "M2",
        "label": "Research on transformations",
        "definition": "Analyses societal change.",
    }
    assert "pathway:  # tag only when explicit" in text  # unchanged block kept


def test_strategy_terms_unknown_code_is_a_problem(files: dict[str, Path]):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS)
    _append_row(
        files["wb"],
        "strategy_terms",
        {"dimension": "research_mode", "code": "M9", "label": "X"},
    )
    with pytest.raises(CurationError, match="unknown code M9"):
        apply(files)


def test_carry_into_a_later_round_keeps_only_open_candidates(
    files: dict[str, Path], tmp_path: Path
):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    decisions = {
        **DECISIONS,
        "cand:cccccccccc": {"decision": "defer", "comment": "later"},
    }
    fill(files["wb"], decisions)
    apply(files)  # the vocabulary is now v0.3, the workbook's base v0.2
    newer = tmp_path / "v0.4.xlsx"
    export_workbook(files["vocab"], files["cands"], newer, carry_from=files["wb"])
    rows = read_workbook(newer)
    keys = {r["key"]: r for sheet in rows.values() for r in sheet if "key" in r}
    assert "cand:aaaaaaaaaa" not in keys and "cand:bbbbbbbbbb" not in keys
    assert keys["cand:cccccccccc"]["decision"] == "defer"
    assert keys["cand:cccccccccc"]["comment"] == "later"
    # terms come from the new vocabulary, preset to keep
    assert keys["term:Interviews and focus group discussions"]["decision"] == "keep"
    assert "term:Transdisciplinary research" in keys
    assert keys["term:Transdisciplinary research"]["comment"] == ""
    assert keys["term:Interviews and focus group discussions"]["comment"] == ""


def test_new_code_for_a_kept_term_retires_the_old_one(
    files: dict[str, Path], tmp_path: Path
):
    export_workbook(files["vocab"], files["cands"], files["wb"])
    fill(files["wb"], DECISIONS)
    apply(files)  # v0.3: every term has a code
    newer = tmp_path / "v0.4.xlsx"
    export_workbook(files["vocab"], files["cands"], newer, carry_from=files["wb"])
    fill(newer, {"term:Pastoralists": {"code": "ACT-herders"}})
    apply_workbook(
        newer,
        files["vocab"],
        version="0.4",
        terms_range=(1, 40),
        code_map_path=tmp_path / "map.csv",
    )
    actors = yaml.safe_load(files["vocab"].read_text(encoding="utf-8"))["actors"]
    assert [t["code"] for t in actors["terms"]] == ["ACT-herders"]
    assert actors["retired"] == [
        {
            "code": "ACT-pastoralists",
            "label": "Pastoralists",
            "replaced_by": "ACT-herders",
        }
    ]
