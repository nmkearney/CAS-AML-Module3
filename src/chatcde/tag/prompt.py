"""The system prompt for one tagging batch, built from the template and the vocabulary.

The template (``tagging.prompt``, e.g. ``prompts/tag.v1.md``) has two parts:
general rules with a ``{{dimensions}}`` placeholder, and after the marker line
:data:`RULES_MARKER` one ``### <dimension>`` section of rules per dimension.
For a batch, the code puts in each of its dimensions: the rules, then the terms.

Terms are listed the way the vocabulary groups them (decision 0028): under group
headings for the curated dimensions, by impact area for sub-areas and additional
sub-areas, flat for the rest. Each line is ``code — label: definition`` plus a
few synonyms (``includes``), so the model sees what each code covers. Countries
are not listed (there are 248); the JSON schema restricts them to valid codes.
"""

from __future__ import annotations

import re
from typing import Any

from chatcde.config import TaggingConfig
from chatcde.schemas import Dimension
from chatcde.tag.answer import OTHER, allows_other
from chatcde.tag.curation import PREFIXES, current_terms
from chatcde.tag.llm import fill_prompt
from chatcde.tools.vocabulary import SDG_NAMES, dimension_entries

RULES_MARKER = "<!-- dimension rules"


def split_template(template: str) -> tuple[str, dict[str, str]]:
    """The general part of the template and the rules per dimension.

    Raises:
        ValueError: The marker line is missing.
    """
    if RULES_MARKER not in template:
        raise ValueError(f"tagging prompt has no '{RULES_MARKER}' line")
    head, _, tail = template.partition(RULES_MARKER)
    tail = tail.split("\n", 1)[1] if "\n" in tail else ""
    rules: dict[str, str] = {}
    for m in re.finditer(r"^### (\w+)\n(.*?)(?=^### |\Z)", tail, re.S | re.M):
        rules[m.group(1)] = m.group(2).strip()
    return head.strip(), rules


def _line(code: str, label: str, definition: str | None, includes: list[str]) -> str:
    """One term line: ``- code — label: definition (also: a; b)``."""
    text = f"- {code} — {label}" if label and label != code else f"- {code}"
    if definition:
        text += f": {definition.strip()}"
    if includes:
        text += f" (also: {'; '.join(includes)})"
    return text


def _curated(dimension: str, vocab: dict[str, Any], n_includes: int) -> list[str]:
    """Curated terms under their group headings, in vocabulary order."""
    terms = current_terms(vocab.get(dimension))
    groups: dict[str, list[str]] = {}
    for t in terms:
        line = _line(t.code or t.label, t.label, t.definition, t.includes[:n_includes])
        groups.setdefault(t.group or "", []).append(line)
    out: list[str] = []
    for group, lines in groups.items():
        if group:
            out.append(f"#### {group}")
        out.extend(lines)
    return out


def _sub_areas(vocab: dict[str, Any], n_includes: int) -> list[str]:
    """Strategy sub-areas under their impact areas, with scope notes."""
    areas = {str(a["code"]): str(a["label"]) for a in vocab.get("impact_area") or []}
    out: list[str] = []
    current = None
    for s in vocab.get("sub_area") or []:
        area = str(s["code"]).split("-")[0]
        if area != current:
            out.append(f"#### {area}: {areas.get(area, area)}")
            current = area
        includes = list(s.get("includes") or [])[:n_includes]
        out.append(_line(s["code"], s["label"], s.get("scope"), includes))
    return out


def _additional(vocab: dict[str, Any], n_includes: int) -> list[str]:
    """Additional sub-areas grouped by impact area (the parent field)."""
    areas = {str(a["code"]): str(a["label"]) for a in vocab.get("impact_area") or []}
    names = {**areas, "cross_cutting": "Cross-cutting", "outside": "Outside the areas"}
    groups: dict[str, list[str]] = {}
    for t in (vocab.get("additional_sub_area") or {}).get("terms") or []:
        line = _line(
            t["code"],
            t["label"],
            t.get("definition"),
            list(t.get("includes") or [])[:n_includes],
        )
        groups.setdefault(str(t.get("impact_area")), []).append(line)
    out: list[str] = []
    for key in [*areas, "cross_cutting", "outside"]:
        if key in groups:
            out.append(f"#### {names[key]}")
            out.extend(groups[key])
    return out


def term_lines(
    dimension: str, vocab: dict[str, Any], settings: TaggingConfig
) -> list[str]:
    """The list of terms shown for one dimension."""
    n = settings.includes_shown
    dim = Dimension(dimension)
    if dim is Dimension.SUB_AREA:
        return _sub_areas(vocab, n)
    if dim is Dimension.ADDITIONAL_SUB_AREA:
        return _additional(vocab, n)
    if dimension in PREFIXES:
        return _curated(dimension, vocab, n)
    if dim is Dimension.COUNTRY:
        return []
    if dim is Dimension.SDG:
        return [f"- {k} — {v}" for k, v in SDG_NAMES.items()]
    lines = []
    for e in dimension_entries(vocab, dim):
        node = vocab.get(dimension)
        extra = ""
        if isinstance(node, list):
            item = next(
                (x for x in node if isinstance(x, dict) and x.get("code") == e.code),
                None,
            )
            if item and item.get("examples"):
                extra = f" Examples: {item['examples']}"
        lines.append(_line(e.code, e.label, (e.definition or "") + extra, []))
    return lines


def system_prompt(
    template: str,
    dimensions: list[str],
    vocab: dict[str, Any],
    settings: TaggingConfig,
) -> str:
    """The full system prompt for a batch of dimensions.

    Raises:
        ValueError: The template lacks rules for one of the dimensions.
    """
    head, rules = split_template(template)
    blocks = []
    for dim in dimensions:
        if dim not in rules:
            raise ValueError(f"tagging prompt has no '### {dim}' rules")
        title = f"## {dim} (at most {settings.limit(dim)} tags)"
        lines = [title, rules[dim], *term_lines(dim, vocab, settings)]
        if allows_other(dim):
            lines.append(f"- {OTHER} — none of the above fits (give other_label)")
        blocks.append("\n".join(lines))
    return fill_prompt(head, {"dimensions": "\n\n".join(blocks)})
