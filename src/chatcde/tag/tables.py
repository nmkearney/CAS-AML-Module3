"""Catalogue tables private to Track A's vocabulary and tagging stages.

``vocab extract`` uses the ``vocab_*`` tables; ``chatcde tag`` uses ``tag_pilot``,
``tag_runs`` and ``tag_state`` (decision 0028). They are working tables, not part
of the shared contract in :mod:`chatcde.schemas`: no tool or Track B code reads
them. ``chatcde tag`` publishes one run to the shared ``tags`` table. The column layout
still comes from pydantic models through :func:`chatcde.catalogue.create_table_sql`,
so they follow the same type mapping as the shared tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import duckdb
from pydantic import BaseModel

from chatcde.catalogue import create_table_sql, to_row
from chatcde.schemas import Tag


class SampleRow(BaseModel):
    """One sampled record and the stratum it was drawn from (``vocab_sample``)."""

    record_id: str
    stratum: str  # "unit|decade|language"
    unit: str
    decade: str
    language: str
    has_pages: bool
    seed: int
    sample_size: int
    stratum_floor: int
    sampled_at: datetime
    code_version: str


class LabelRow(BaseModel):
    """One free-text label the model wrote for a record (``vocab_labels``)."""

    record_id: str
    kind: str  # method, theme, data_type, setting, system, actors
    label: str
    label_norm: str
    evidence: str | None = None
    evidence_found: bool
    role: str | None = None  # actors only
    model: str
    prompt_version: str
    code_version: str
    created_at: datetime


class LabelStateRow(BaseModel):
    """Outcome of labelling a record with a model and prompt (``vocab_label_state``)."""

    record_id: str
    model: str
    prompt_version: str
    status: str  # ok | error
    n_labels: int
    sources: str  # e.g. "abstract,fulltext"
    input_chars: int
    error: str | None = None
    code_version: str
    created_at: datetime


class LabelVectorRow(BaseModel):
    """Cached embedding of a normalised label (``vocab_label_vectors``)."""

    label_norm: str
    model: str
    dimensions: int
    vector: list[float]


class ProposalRow(BaseModel):
    """Cached LLM proposal for one cluster of labels (``vocab_proposals``)."""

    cluster_key: str  # sha256 of kind + member labels
    kind: str
    model: str
    prompt_version: str
    proposal_json: str
    code_version: str
    created_at: datetime


class TagRunRow(Tag):
    """One tag from a tagging run (``tag_runs``): a :class:`Tag` plus run and batch."""

    run: str  # e.g. "main", "alt"; "main" is published to ``tags``
    batch: str  # name in ``tagging.batches``


class TagStateRow(BaseModel):
    """Outcome of tagging one record in one batch of one run (``tag_state``)."""

    record_id: str
    run: str
    batch: str
    model: str
    prompt_version: str
    vocab_version: str
    status: str  # ok | error
    n_tags: int
    n_unverified: int  # tags whose quote was not found in the input
    sources: str  # e.g. "abstract,fulltext" or "metadata"
    input_chars: int
    error: str | None = None
    code_version: str
    created_at: datetime


TABLES: dict[str, type[BaseModel]] = {
    "vocab_sample": SampleRow,
    "vocab_labels": LabelRow,
    "vocab_label_state": LabelStateRow,
    "vocab_label_vectors": LabelVectorRow,
    "vocab_proposals": ProposalRow,
    "tag_pilot": SampleRow,  # same columns as vocab_sample; seed = tagging.pilot.seed
    "tag_runs": TagRunRow,
    "tag_state": TagStateRow,
}


def ensure(con: duckdb.DuckDBPyConnection, *names: str) -> None:
    """Create the named tables if they do not exist (existing rows are kept)."""
    for name in names:
        con.execute(create_table_sql(name, TABLES[name], if_not_exists=True))


def insert(
    con: duckdb.DuckDBPyConnection, table: str, rows: Sequence[BaseModel]
) -> None:
    """Append model rows to one of this module's tables."""
    if not rows:
        return
    marks = ", ".join("?" for _ in TABLES[table].model_fields)
    con.executemany(f"INSERT INTO {table} VALUES ({marks})", [to_row(r) for r in rows])


def has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether the catalogue has a table called ``name``."""
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [name],
        ).fetchone()[0]  # type: ignore[index]
    )
