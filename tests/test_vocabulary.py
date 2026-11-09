"""Checks on the committed vocabulary (``vocab/draft_vocabulary.yaml``).

``chatcde vocab apply`` validates the workbook before it writes the YAML; these
tests guard the result in git, so a hand edit or a bad merge cannot break the
codes that tags store (decisions 0026 and 0027).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from chatcde.schemas import Dimension
from chatcde.tag.curation import ASA_PARENTS, PREFIXES, code_ok, group_code
from chatcde.tools.vocabulary import dimension_entries

VOCAB = Path(__file__).parents[1] / "vocab" / "draft_vocabulary.yaml"


@pytest.fixture(scope="module")
def vocab() -> dict[str, Any]:
    return yaml.safe_load(VOCAB.read_text(encoding="utf-8"))


def live_codes(vocab: dict[str, Any]) -> set[str]:
    """Every code a tag or a link may point to: curated terms and strategy codes."""
    codes = {t["code"] for dim in PREFIXES for t in vocab[dim]["terms"]}
    for key in ("impact_area", "sub_area", "objective", "pathway", "research_mode"):
        codes |= {str(x["code"]) for x in vocab.get(key) or [] if isinstance(x, dict)}
    return codes


def test_every_dimension_is_in_the_file(vocab: dict[str, Any]) -> None:
    assert {d.value for d in Dimension} <= set(vocab)


@pytest.mark.parametrize("dim", list(PREFIXES))
def test_codes_are_well_formed_and_unique(vocab: dict[str, Any], dim: str) -> None:
    block = vocab[dim]
    codes = [t["code"] for t in block["terms"]]
    assert codes and all(code_ok(dim, c) for c in codes), dim
    assert len(codes) == len(set(codes))
    assert all(t.get("label") for t in block["terms"])
    groups = {g["code"] for g in block.get("groups") or []}
    assert all(g.startswith(f"{PREFIXES[dim]}-G-") for g in groups)
    assert {t["group"] for t in block["terms"] if t.get("group")} <= groups
    assert all(
        group_code(dim, g["label"]) == g["code"] for g in block.get("groups") or []
    )


def test_codes_are_unique_across_dimensions(vocab: dict[str, Any]) -> None:
    codes = [t["code"] for dim in PREFIXES for t in vocab[dim]["terms"]]
    assert len(codes) == len(set(codes))


def test_retired_codes_are_not_live_and_point_to_live_codes(
    vocab: dict[str, Any],
) -> None:
    live = live_codes(vocab)
    for dim in PREFIXES:
        for r in vocab[dim].get("retired") or []:
            assert r["code"] not in live, r
            assert r["replaced_by"] is None or r["replaced_by"] in live, r


def test_related_links_point_to_live_codes(vocab: dict[str, Any]) -> None:
    live = live_codes(vocab)
    for dim in PREFIXES:
        for t in vocab[dim]["terms"]:
            for code in t.get("related") or []:
                assert code in live and code != t["code"], (t["code"], code)


def test_additional_sub_areas(vocab: dict[str, Any]) -> None:
    sub_area_labels = {s["label"].lower() for s in vocab["sub_area"]}
    for t in vocab["additional_sub_area"]["terms"]:
        assert t.get("impact_area") in ASA_PARENTS, t["code"]
        assert t["label"].lower() not in sub_area_labels, t["code"]


@pytest.mark.parametrize("dim", list(PREFIXES))
def test_tools_list_the_curated_terms(vocab: dict[str, Any], dim: str) -> None:
    """Track B's reader sees exactly the live terms, not groups or retired codes."""
    entries = dimension_entries(vocab, Dimension(dim))
    assert [e.code for e in entries] == [t["code"] for t in vocab[dim]["terms"]]
