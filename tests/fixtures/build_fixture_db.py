"""Build ``fixture.duckdb`` from the fixture JSONL files.

Each JSONL file maps to one catalogue table. Table names and the column layout
come from :mod:`chatcde.catalogue`, which derives them from the pydantic models,
so the fixture database always matches ``schemas.py`` and the real catalogue.
Every JSONL line is validated through its model before insertion. Building is
idempotent: tables are dropped and recreated. See docs/decisions/0007-test-fixtures.md.

    python tests/fixtures/build_fixture_db.py [--db tests/fixtures/fixture.duckdb]

Tests use the ``fixture_db`` fixture in ``tests/conftest.py`` instead, which builds
a fresh copy in a temp folder.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
from pydantic import BaseModel

from chatcde.catalogue import TABLES as CATALOGUE_TABLES
from chatcde.catalogue import create_table_sql, insert

HERE = Path(__file__).resolve().parent

#: table name -> (JSONL file name, model). The fixtures have no ``chunks``.
TABLES: dict[str, tuple[str, type[BaseModel]]] = {
    name: (f"{name}.jsonl", CATALOGUE_TABLES[name])
    for name in ("records", "authors", "tags", "pages", "downloads")
}


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
            insert(con, table, rows)
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
