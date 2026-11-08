"""Read the draft vocabulary YAML (``vocab.file``) for the tagging stages.

``vocab/draft_vocabulary.yaml`` mixes several shapes, because each dimension was
written in the form that suits it: lists of strings (``setting``), lists of
mappings with ``code`` and ``label`` (``sub_area``), and groups of lists
(``method``, ``system``, ``actors.groups``). The helpers here turn any of them
into flat term lists, so callers do not depend on the layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_vocabulary(path: Path) -> dict[str, Any]:
    """Load the vocabulary YAML as a mapping.

    Raises:
        ValueError: The file does not hold a mapping at the top level.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def _term(item: Any) -> str | None:
    """One term as text: ``"code: label"`` for mappings, the string itself otherwise."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and "label" in item:
        code = item.get("code")
        return f"{code}: {item['label']}" if code else str(item["label"])
    return None


def flatten_terms(node: Any) -> list[str]:
    """All terms under a dimension's YAML node, in file order.

    Handles a list of strings, a list of ``{code, label}`` mappings, a mapping of
    group name -> list, ``actors`` (whose terms sit under ``groups``), and the
    curated layout ``{groups: [...], terms: [...]}`` written by ``vocab apply``. Keys
    that are settings rather than terms (``roles``, ``standard``) are skipped.
    """
    if isinstance(node, list):
        return [t for t in (_term(i) for i in node) if t]
    if isinstance(node, dict):
        if "terms" in node:  # layout written by `chatcde vocab apply` (0026)
            return flatten_terms(node["terms"])
        if "groups" in node:
            return flatten_terms(node["groups"])
        terms: list[str] = []
        for value in node.values():
            if isinstance(value, list):
                terms.extend(flatten_terms(value))
        return terms
    return []


def seed_terms(vocab: dict[str, Any], dimension: str) -> list[str]:
    """The current terms of one dimension, or ``[]`` if it is not in the file."""
    return flatten_terms(vocab.get(dimension))


def unit_codes(vocab: dict[str, Any]) -> dict[str, str]:
    """Map BORIS unit names to impact-area codes, e.g. Sustainable Land Systems -> SL.

    Read from the ``boris_unit`` field of ``impact_area``. Units without an
    impact area (``Services``) are not in the map; callers use a slug instead.
    """
    out: dict[str, str] = {}
    for item in vocab.get("impact_area") or []:
        if isinstance(item, dict) and item.get("boris_unit") and item.get("code"):
            out[str(item["boris_unit"])] = str(item["code"])
    return out
