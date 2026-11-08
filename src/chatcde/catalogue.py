"""Catalogue tables in DuckDB, derived from the models in :mod:`chatcde.schemas`.

Both tracks read and write ``data/catalogue.duckdb``; this module is the single
place that turns a pydantic model into a table, so the layout always follows
``schemas.py``. It is shared code: changes go through an issue labelled
``schemas`` (see CONTRIBUTING.md). The test fixture database
(``tests/fixtures/build_fixture_db.py``) is built with the same functions.

==============================  ===================
Python type                     DuckDB type
==============================  ===================
``str``, ``str`` enums          ``VARCHAR``
``int``                         ``BIGINT``
``float``                       ``DOUBLE``
``bool``                        ``BOOLEAN``
``datetime``                    ``TIMESTAMP`` (UTC)
``list[str]`` / ``list[int]``   ``VARCHAR[]`` / ``BIGINT[]``
``dict[str, str]``              ``MAP(VARCHAR, VARCHAR)``
nested model (``Provenance``)   ``STRUCT(...)``
==============================  ===================

Timestamps are converted to UTC and stored without a zone: reading
``TIMESTAMPTZ`` back into Python would need ``pytz``. Optional fields become
nullable columns; all others are ``NOT NULL``.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from chatcde.schemas import Author, Chunk, ManifestEntry, PageText, Record, Tag

#: Table name -> model, as listed in the :mod:`chatcde.schemas` docstring.
TABLES: dict[str, type[BaseModel]] = {
    "records": Record,
    "authors": Author,
    "tags": Tag,
    "pages": PageText,
    "downloads": ManifestEntry,
    "chunks": Chunk,
}

_SCALARS: dict[type, str] = {
    str: "VARCHAR",
    int: "BIGINT",
    float: "DOUBLE",
    bool: "BOOLEAN",
    datetime: "TIMESTAMP",
}


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    """Return (inner type, nullable) for ``X | None``; other types pass through."""
    if typing.get_origin(tp) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
        raise TypeError(f"unsupported union {tp}")
    return tp, False


def duck_type(tp: Any) -> str:
    """DuckDB column type for a Python annotation (see the module docstring)."""
    tp, _ = _unwrap_optional(tp)
    origin = typing.get_origin(tp)
    if origin is list:
        (inner,) = typing.get_args(tp)
        return duck_type(inner) + "[]"
    if origin is dict:
        k, v = typing.get_args(tp)
        return f"MAP({duck_type(k)}, {duck_type(v)})"
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        fields = ", ".join(
            f"{name} {duck_type(f.annotation)}" for name, f in tp.model_fields.items()
        )
        return f"STRUCT({fields})"
    if isinstance(tp, type) and issubclass(tp, Enum):
        return "VARCHAR"
    for py, sql in _SCALARS.items():
        if tp is py:
            return sql
    raise TypeError(f"no DuckDB type for {tp!r}")


def create_table_sql(
    table: str, model: type[BaseModel], *, if_not_exists: bool = False
) -> str:
    """``CREATE TABLE`` statement for one model."""
    cols = []
    for name, f in model.model_fields.items():
        _, nullable = _unwrap_optional(f.annotation)
        null = "" if nullable else " NOT NULL"
        cols.append(f"{name} {duck_type(f.annotation)}{null}")
    guard = "IF NOT EXISTS " if if_not_exists else ""
    return f"CREATE TABLE {guard}{table} (\n  " + ",\n  ".join(cols) + "\n)"


def to_row(obj: BaseModel) -> list[Any]:
    """Model instance -> list of column values (enums as values, datetimes in UTC)."""

    def conv(v: Any) -> Any:
        if isinstance(v, Enum):
            return v.value
        if isinstance(v, datetime) and v.tzinfo is not None:
            return v.astimezone(UTC).replace(tzinfo=None)
        if isinstance(v, BaseModel):
            return {k: conv(x) for k, x in v}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    return [conv(v) for _, v in obj]


def connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the catalogue, creating its folder first when writing."""
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """Column names of ``table`` in order; empty if the table does not exist."""
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def ensure_tables(con: duckdb.DuckDBPyConnection, names: Iterable[str]) -> None:
    """Create the named tables if they do not exist yet (existing data is kept)."""
    for name in names:
        con.execute(create_table_sql(name, TABLES[name], if_not_exists=True))


def insert(
    con: duckdb.DuckDBPyConnection, table: str, rows: Sequence[BaseModel]
) -> None:
    """Append validated model rows to ``table`` (column order = model field order)."""
    if not rows:
        return
    model = TABLES[table]
    marks = ", ".join("?" for _ in model.model_fields)
    con.executemany(f"INSERT INTO {table} VALUES ({marks})", [to_row(r) for r in rows])


def replace_tables(
    con: duckdb.DuckDBPyConnection, tables: dict[str, Sequence[BaseModel]]
) -> None:
    """Make each named table hold exactly the given rows, in one transaction.

    Used by stages that rebuild whole tables from their own cache (for example
    ``chatcde harvest metadata`` for ``records`` and ``authors``). Either every
    table is replaced or, on any error, all are left as they were.

    A table whose columns no longer match its model (a field was added to
    ``schemas.py``) is dropped and created again; its rows are replaced anyway,
    so nothing is lost and old catalogues need no separate migration.
    """
    ensure_tables(con, tables)
    con.execute("BEGIN TRANSACTION")
    try:
        for table, rows in tables.items():
            model = TABLES[table]
            if columns(con, table) != list(model.model_fields):
                con.execute(f"DROP TABLE {table}")
                con.execute(create_table_sql(table, model))
            else:
                con.execute(f"DELETE FROM {table}")
            insert(con, table, rows)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
