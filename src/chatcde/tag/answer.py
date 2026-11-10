"""The JSON schema the tagging model answers in, built per batch from the vocabulary.

Each batch of dimensions (``tagging.batches``) gets its own pydantic model, sent
as ``response_format`` by :func:`chatcde.tag.llm.chat_json`, so the server only
lets the model write codes that exist (decision 0028):

* ``term`` is a ``Literal`` of the live codes of that dimension, read with
  :func:`chatcde.tools.vocabulary.dimension_entries`, so groups and retired codes
  are never offered. Countries come from ``vocab/m49.csv`` plus the
  global/multi-country flag; SDGs are "1"–"17"; output type is the audience list.
* Curated dimensions (method, setting, actors, …) also allow ``"other"``, with a
  short ``other_label``, so gaps in the vocabulary show up for its next version.
* ``strength`` (explicit / inferred / weak) is mapped to a confidence in code
  (``tagging.confidence``); the model never writes a number.
* ``role`` (studied / co_producer / audience) is required for actors only.
* Each list is capped at ``tagging.max_per_dimension``.

Every field is required (nullable where it may be empty) because vLLM's strict
JSON-schema mode expects that.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from chatcde.config import TaggingConfig
from chatcde.schemas import ActorRole, Dimension
from chatcde.tag.curation import PREFIXES
from chatcde.tag.regions import GLOBAL_FLAG, Country
from chatcde.tools.vocabulary import dimension_entries

OTHER = "other"
Strength = Literal["explicit", "inferred", "weak"]


def allows_other(dimension: str) -> bool:
    """Whether a dimension accepts ``other`` (the curated ones, decision 0026)."""
    return dimension in PREFIXES


def codes_for(
    dimension: str,
    vocab: dict[str, Any],
    countries: dict[str, Country],
    exclude: Collection[str] = (),
) -> list[str]:
    """Codes the model may use for one dimension, in vocabulary order.

    ``exclude`` drops codes filled in by code instead (``tagging.none_terms``).

    Raises:
        ValueError: The dimension has no codes (missing from the vocabulary).
    """
    dim = Dimension(dimension)
    if dim is Dimension.COUNTRY:
        codes = sorted(countries) + [GLOBAL_FLAG]
    else:
        codes = [e.code for e in dimension_entries(vocab, dim) if e.code not in exclude]
    if allows_other(dimension):
        codes.append(OTHER)
    if not codes:
        raise ValueError(f"no vocabulary codes for {dimension}")
    return codes


def item_model(dimension: str, codes: list[str]) -> type[BaseModel]:
    """The model of one tag of ``dimension`` (term, evidence, strength, …)."""
    fields: dict[str, Any] = {
        "term": (
            Literal[tuple(codes)],  # type: ignore[valid-type]
            Field(description=f"A {dimension} code from the list"),
        ),
        "evidence": (
            str,
            Field(description="Short verbatim quote from the text, original language"),
        ),
        "strength": (
            Strength,
            Field(description="explicit: stated; inferred: clearly implied; weak"),
        ),
    }
    if allows_other(dimension):
        fields["other_label"] = (
            str | None,
            Field(description="Short label when term is 'other', else null"),
        )
    if dimension == Dimension.ACTORS.value:
        fields["role"] = (
            Literal[tuple(r.value for r in ActorRole)],  # type: ignore[valid-type]
            Field(description="How the actors relate to the research"),
        )
    name = "".join(p.title() for p in dimension.split("_")) + "Tag"
    return create_model(
        name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def batch_model(
    batch: str,
    dimensions: list[str],
    vocab: dict[str, Any],
    countries: dict[str, Country],
    settings: TaggingConfig,
) -> type[BaseModel]:
    """The answer model for one batch: one capped list of tags per dimension."""
    fields: dict[str, Any] = {}
    for dim in dimensions:
        none = settings.none_terms.get(dim)
        codes = codes_for(dim, vocab, countries, exclude={none} if none else ())
        item = item_model(dim, codes)
        fields[dim] = (list[item], Field(max_length=settings.limit(dim)))  # type: ignore[valid-type]
    name = "Tags" + "".join(p.title() for p in batch.split("_"))
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)
