"""Catalogue tables private to the vocabulary-drafting stage (``vocab extract``).

They are working tables, not part of the shared contract in
:mod:`chatcde.schemas`: no tool or Track B code reads them. The column layout
still comes from pydantic models through :func:`chatcde.catalogue.create_table_sql`,
so they follow the same type mapping as the shared tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import duckdb
from pydantic import BaseModel

from chatcde.catalogue import create_table_sql, to_row


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


TABLES: dict[str, type[BaseModel]] = {
    "vocab_sample": SampleRow,
    "vocab_labels": LabelRow,
    "vocab_label_state": LabelStateRow,
    "vocab_label_vectors": LabelVectorRow,
    "vocab_proposals": ProposalRow,
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
