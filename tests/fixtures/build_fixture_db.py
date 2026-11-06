"""Build ``fixture.duckdb`` from the fixture JSONL files.

Each JSONL file maps to one catalogue table, using the table names from the
``chatcde.schemas`` module docstring. The column layout is derived from the
pydantic models rather than written by hand, so it follows ``schemas.py``
automatically:

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
nullable columns; all others are ``NOT NULL``. Every JSONL line is validated
through its model before insertion. Building is idempotent: tables are dropped
and recreated. See docs/decisions/0007-test-fixtures.md.

    python tests/fixtures/build_fixture_db.py [--db tests/fixtures/fixture.duckdb]

Tests use the ``fixture_db`` fixture in ``tests/conftest.py`` instead, which builds
a fresh copy in a temp folder.
"""

from __future__ import annotations

import argparse
import types
import typing
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from chatcde.schemas import Author, ManifestEntry, PageText, Record, Tag

HERE = Path(__file__).resolve().parent

#: table name -> (JSONL file name, model). Order matters only for readability.
TABLES: dict[str, tuple[str, type[BaseModel]]] = {
    "records": ("records.jsonl", Record),
    "authors": ("authors.jsonl", Author),
    "tags": ("tags.jsonl", Tag),
    "pages": ("pages.jsonl", PageText),
    "downloads": ("downloads.jsonl", ManifestEntry),
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


def create_table_sql(table: str, model: type[BaseModel]) -> str:
    """``CREATE TABLE`` statement for one model."""
    cols = []
    for name, f in model.model_fields.items():
        _, nullable = _unwrap_optional(f.annotation)
        null = "" if nullable else " NOT NULL"
        cols.append(f"{name} {duck_type(f.annotation)}{null}")
    return f"CREATE TABLE {table} (\n  " + ",\n  ".join(cols) + "\n)"


def load_jsonl(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    """Validate every non-empty line of a JSONL file against ``model``.

    Raises ``pydantic.ValidationError`` with the line number in the message
    context if a line does not fit the schema. A missing file gives no rows.
    """
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(model.model_validate_json(line))
            except Exception as e:
                raise ValueError(f"{path.name}:{n}: {e}") from e
    return rows


def _row(obj: BaseModel) -> list[Any]:
    """Model instance -> list of column values (enums as their string value)."""

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


def build(fixtures_dir: Path = HERE, db_path: Path | None = None) -> Path:
    """(Re)build the fixture DuckDB from the JSONL files in ``fixtures_dir``.

    Args:
        fixtures_dir: Folder with ``records.jsonl`` etc.
        db_path: Output file; defaults to ``fixtures_dir / "fixture.duckdb"``.

    Returns:
        The path of the written database.
    """
    db_path = db_path or fixtures_dir / "fixture.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        for table, (file_name, model) in TABLES.items():
            rows = load_jsonl(fixtures_dir / file_name, model)
            con.execute(f"DROP TABLE IF EXISTS {table}")
            con.execute(create_table_sql(table, model))
            if rows:
                marks = ", ".join("?" for _ in model.model_fields)
                con.executemany(
                    f"INSERT INTO {table} VALUES ({marks})", [_row(r) for r in rows]
                )
    finally:
        con.close()
    return db_path


def main() -> None:
    """Command-line entry: build the DB and print row counts."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=HERE / "fixture.duckdb")
    args = ap.parse_args()
    path = build(HERE, args.db)
    con = duckdb.connect(str(path), read_only=True)
    for table in TABLES:
        (n,) = con.execute(f"SELECT count(*) FROM {table}").fetchone()
        print(f"  {table:10} {n:5d}")
    con.close()
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
