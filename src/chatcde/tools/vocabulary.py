"""Read the controlled vocabulary (``vocab.file``) as coded terms with definitions.

``vocab/draft_vocabulary.yaml`` writes each dimension in the shape that suits it
(decision 0021):

* a list of strings (``setting``, ``justice_lens``): the string is code and label;
* a list of mappings with ``code`` and ``label`` (``sub_area``, ``pathway``): the
  definition comes from ``definition``, ``scope`` or ``rule``;
* groups of lists (``method``, ``system``, ``actors.groups``; up to v0.2): the
  group name becomes the definition;
* the curated layout written by ``chatcde vocab apply`` (v0.3, decision 0026):
  ``{groups: [{code, label}], terms: [{code, label, group, definition, ...}],
  retired: [...]}``. Only ``terms`` are entries, each with its ``group`` code;
  retired codes are not listed;
* standards: ``country`` (ISO 3166-1 alpha-3 plus a flag term), ``sdg`` (UN goals
  1-17, labelled here), ``region`` (CDE partner regions; UN M49 sub-regions are
  derived from countries and are listed as they occur in the tags), and
  ``output_type`` (its ``audience`` list).

The codes are what the ``tags`` table stores in ``term``. This module is for
listing and counting; the tagging stage has its own reader (``chatcde.tag``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from chatcde.schemas import Dimension, Filters

#: Short names of the 17 Sustainable Development Goals (United Nations).
SDG_NAMES: dict[int, str] = {
    1: "No poverty",
    2: "Zero hunger",
    3: "Good health and well-being",
    4: "Quality education",
    5: "Gender equality",
    6: "Clean water and sanitation",
    7: "Affordable and clean energy",
    8: "Decent work and economic growth",
    9: "Industry, innovation and infrastructure",
    10: "Reduced inequalities",
    11: "Sustainable cities and communities",
    12: "Responsible consumption and production",
    13: "Climate action",
    14: "Life below water",
    15: "Life on land",
    16: "Peace, justice and strong institutions",
    17: "Partnerships for the goals",
}

#: Keys of a term mapping that can hold its definition, in order of preference.
_DEFINITION_KEYS = ("definition", "scope", "rule")


@dataclass(frozen=True)
class Entry:
    """One vocabulary term: the code stored in tags, a label and a definition."""

    code: str
    label: str
    definition: str | None = None
    group: str | None = None


class VocabularyError(Exception):
    """The vocabulary file is missing or not a YAML mapping."""


def load_vocabulary(path: Path) -> dict[str, Any]:
    """Load the vocabulary YAML as a mapping.

    Raises:
        VocabularyError: If the file is missing or not a mapping.
    """
    if not path.is_file():
        raise VocabularyError(f"no vocabulary file at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise VocabularyError(f"{path} must contain a mapping at the top level")
    return data


def _humanise(group: str) -> str:
    """'participatory_transdisciplinary' -> 'Participatory transdisciplinary'."""
    text = group.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _from_item(item: Any, group: str | None = None) -> Entry | None:
    """An entry from a string or a ``{code, label, ...}`` mapping."""
    note = f"Group: {_humanise(group)}" if group else None
    if isinstance(item, str | int | float) and not isinstance(item, bool):
        return Entry(str(item), str(item), note)
    if isinstance(item, dict) and ("label" in item or "code" in item):
        code = str(item.get("code") or item["label"])
        label = str(item.get("label") or code)
        definition = next((str(item[k]) for k in _DEFINITION_KEYS if item.get(k)), None)
        if definition is None and item.get("boris_unit"):
            definition = f"BORIS unit: {item['boris_unit']}"
        if note:
            definition = f"{note}. {definition}" if definition else note
        return Entry(code, label, definition)
    return None


def _from_list(items: list[Any], group: str | None = None) -> list[Entry]:
    """Entries from a list of strings or term mappings."""
    return [e for e in (_from_item(i, group) for i in items) if e]


def _from_groups(groups: dict[str, Any]) -> list[Entry]:
    """Entries from a mapping of group name -> list of terms."""
    out: list[Entry] = []
    for name, items in groups.items():
        if isinstance(items, list):
            out.extend(_from_list(items, name))
    return out


def _curated(node: dict[str, Any]) -> list[Entry]:
    """Entries from the curated layout: ``terms``, each with its group code."""
    out = []
    for t in node["terms"]:
        group = t.get("group") or t.get("impact_area")
        out.append(
            Entry(
                str(t["code"]),
                str(t.get("label") or t["code"]),
                t.get("definition"),
                str(group) if group else None,
            )
        )
    return out


def _regions(node: dict[str, Any]) -> list[Entry]:
    """CDE partner regions with the countries or M49 region they cover."""
    out = []
    for code, spec in (node.get("cde_partner_region") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        if spec.get("countries"):
            cover = "Countries: " + ", ".join(spec["countries"])
        elif spec.get("m49"):
            cover = f"UN M49 region {spec['m49']}"
        else:
            cover = None
        definition = "CDE partner region" + (f"; {cover}" if cover else "")
        out.append(Entry(str(code), str(spec.get("label") or code), definition))
    return out


def dimension_entries(vocab: dict[str, Any], dimension: Dimension) -> list[Entry]:
    """The vocabulary's terms for one dimension, in file order.

    Dimensions whose terms come from a standard list only what the file names
    (country: the flag term; region: the partner regions) plus, for SDGs, all 17
    goals; codes that appear only in the tags are added by the caller.
    """
    node = vocab.get(dimension.value)
    if dimension is Dimension.SDG:
        return [Entry(str(n), f"SDG {n}: {name}") for n, name in SDG_NAMES.items()]
    if dimension is Dimension.COUNTRY:
        flag = node.get("extra_flag") if isinstance(node, dict) else None
        return (
            [Entry(str(flag), "Global or multi-country", "Flag term")] if flag else []
        )
    if dimension is Dimension.REGION and isinstance(node, dict):
        return _regions(node)
    if isinstance(node, dict) and "terms" in node:
        return _curated(node)
    if dimension is Dimension.OUTPUT_TYPE and isinstance(node, dict):
        return _from_list(node.get("audience") or [])
    if dimension is Dimension.ACTORS and isinstance(node, dict):
        return _from_groups(node.get("groups") or {})
    if isinstance(node, list):
        return _from_list(node)
    if isinstance(node, dict):
        return _from_groups(node)
    return []


# ---------------------------------------------------------------------------
# Groups (decision 0030)
# ---------------------------------------------------------------------------

#: Labels of the groups of ``additional_sub_area``: its terms' ``impact_area``.
IMPACT_AREA_GROUPS: dict[str, str] = {
    "cross_cutting": "Cross-cutting",
    "outside": "Outside the four impact areas",
}


def group_map(vocab: dict[str, Any], dimension: Dimension) -> dict[str, str] | None:
    """Term code -> group code for a dimension with groups, else ``None``.

    A dimension has groups when its curated terms carry a ``group``
    (``method``, ``data_type``, ``setting``, ``system``, ``actors`` in v0.3) or,
    for ``additional_sub_area``, an ``impact_area``. Everything else (sub-areas,
    scale, justice lens, countries, SDGs, ...) is counted by term. Groups are
    read from the vocabulary at query time, never stored in the tags, so a
    regrouping needs no re-tagging.
    """
    mapping = {e.code: e.group for e in dimension_entries(vocab, dimension) if e.group}
    return mapping or None


def group_entries(vocab: dict[str, Any], dimension: Dimension) -> list[Entry]:
    """The groups of ``dimension`` (code and label), in vocabulary order.

    Empty if the dimension has no groups. The groups listed in ``groups:``
    (for ``additional_sub_area``: the four impact areas, ``cross_cutting`` and
    ``outside``) come first, even if no term uses them; group codes that terms
    use but the list lacks follow in order of first use.
    """
    mapping = group_map(vocab, dimension)
    if not mapping:
        return []
    node = vocab.get(dimension.value)
    declared = node.get("groups") if isinstance(node, dict) else None
    labels: dict[str, str] = {
        str(g["code"]): str(g.get("label") or g["code"])
        for g in declared or []
        if isinstance(g, dict) and g.get("code")
    }
    if dimension is Dimension.ADDITIONAL_SUB_AREA:
        areas = {
            e.code: e.label for e in dimension_entries(vocab, Dimension.IMPACT_AREA)
        }
        labels |= {**areas, **IMPACT_AREA_GROUPS}
    order = list(dict.fromkeys([*labels, *mapping.values()]))
    return [Entry(code, labels.get(code, code), "Group") for code in order]


def group_members(vocab: dict[str, Any], dimension: Dimension) -> dict[str, list[str]]:
    """Group code -> its term codes, in vocabulary order (empty without groups)."""
    out: dict[str, list[str]] = {}
    for term, group in (group_map(vocab, dimension) or {}).items():
        out.setdefault(group, []).append(term)
    return out


def expand_filters(filters: Filters | None, vocab: dict[str, Any]) -> Filters | None:
    """Replace group codes in tag filters by the codes of their terms.

    ``Filters(methods=["MET-G-biophysical"])`` becomes a filter on every
    biophysical method. Term codes and unknown codes are kept as they are, so a
    filter that names no group is returned unchanged.
    """
    from chatcde.store.filters import TAG_FIELDS

    if filters is None:
        return None
    update: dict[str, list[Any]] = {}
    for field, dimension in TAG_FIELDS.items():
        values = getattr(filters, field)
        members = group_members(vocab, dimension) if values else {}
        if not any(str(v) in members for v in values):
            continue
        expanded: list[Any] = []
        for v in values:
            expanded += members.get(str(v), [v])
        update[field] = list(dict.fromkeys(expanded))
    return filters.model_copy(update=update) if update else filters
