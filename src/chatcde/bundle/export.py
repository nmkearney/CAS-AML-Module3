"""``chatcde export``: build a distributable data bundle (decisions 0004 and 0032).

A bundle is a filtered copy of the local catalogue (which also holds Track B's
chunks, embeddings and BM25 index), a copy of the vocabulary and a
``manifest.json`` (:class:`~chatcde.schemas.BundleManifest`), packed as one
``chatcde-<edition>-<version>.tar.gz``. Next to the archive go a copy of the
manifest (so ``chatcde fetch-data`` can read the version and checksums before it
downloads anything) and a ``.sha256`` file for the archive itself.

Two editions come from the same index, with no re-embedding:

* **full**: every table in :data:`BUNDLE_TABLES`, unfiltered.
* **open**: full text only for records with ``access = 'open'``. For the
  others, pages and full-text chunks (with their vectors, postings and chunk
  state) are left out; abstracts, metadata and tags stay. BM25 computes its
  statistics at query time, so dropping rows needs no re-indexing.

The source catalogue is attached read-only and never written. The archive is
written under a temporary name and renamed at the end, so a failed build leaves
no half-written bundle behind.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from chatcde.config import Config
from chatcde.harvest.common import code_version, now, write_json
from chatcde.schemas import BundleManifest, Edition, TextSource
from chatcde.tools import coverage

log = logging.getLogger(__name__)

#: Tables copied into a bundle, in copy order (``chunks`` before the tables that
#: are filtered by its chunk IDs). Pipeline working tables (``tag_runs``,
#: ``tag_pilot``, ``vocab_*``) stay out; ``tag_state``, ``parse_state`` and
#: ``chunk_state`` go in because they record which model, prompt and code
#: version produced each record's tags, pages and chunks.
BUNDLE_TABLES: tuple[str, ...] = (
    "records",
    "authors",
    "tags",
    "downloads",
    "pages",
    "parse_state",
    "tag_state",
    "chunks",
    "chunk_state",
    "embeddings",
    "bm25_docs",
    "bm25_postings",
)

#: Tables a bundle cannot work without: the catalogue and the search index.
REQUIRED_TABLES: tuple[str, ...] = (
    "records",
    "chunks",
    "embeddings",
    "bm25_docs",
    "bm25_postings",
)

_OPEN_IDS = "SELECT record_id FROM src.records WHERE access = 'open'"
_KEPT_CHUNKS = "SELECT chunk_id FROM main.chunks"

#: WHERE clause per table for the open edition; tables not listed are copied whole.
OPEN_FILTERS: dict[str, str] = {
    "pages": f"record_id IN ({_OPEN_IDS})",
    "parse_state": f"record_id IN ({_OPEN_IDS})",
    # Forget chunk state only where full-text chunks were dropped, so a later
    # `chatcde index chunk` (after --top-up) redoes those records and no others.
    "chunk_state": (
        f"record_id IN ({_OPEN_IDS}) OR record_id NOT IN (SELECT record_id FROM "
        f"src.chunks WHERE source = '{TextSource.FULLTEXT.value}')"
    ),
    "chunks": (
        f"source <> '{TextSource.FULLTEXT.value}' OR record_id IN ({_OPEN_IDS})"
    ),
    "embeddings": f"chunk_id IN ({_KEPT_CHUNKS})",
    "bm25_docs": f"chunk_id IN ({_KEPT_CHUNKS})",
    "bm25_postings": f"chunk_id IN ({_KEPT_CHUNKS})",
}

#: Names of the files inside the archive. Flat, so unpacking into ``data.dir``
#: puts ``manifest.json`` where :func:`chatcde.tools.coverage.local_edition` reads it.
CATALOGUE_NAME = "catalogue.duckdb"
VOCABULARY_NAME = "vocabulary.yaml"
MANIFEST_NAME = coverage.MANIFEST_NAME

#: gzip level: 6 is gzip's own default, a fair trade of time against size.
GZIP_LEVEL = 6


class ExportError(RuntimeError):
    """The bundle cannot be built (missing catalogue or index, failed check)."""


@dataclass
class BundleResult:
    """What one :func:`build_bundle` call produced."""

    edition: Edition
    archive: Path
    manifest: BundleManifest
    skipped: bool = False
    #: table -> (rows in the source, rows in the bundle); empty when skipped.
    tables: dict[str, tuple[int, int]] = field(default_factory=dict)
    chunks: int = 0

    @property
    def archive_bytes(self) -> int:
        """Size of the archive on disk."""
        return self.archive.stat().st_size


def bundle_name(edition: Edition, version: str) -> str:
    """Base name of a bundle's files, e.g. ``chatcde-open-2026.11.10``."""
    return f"chatcde-{edition.value}-{version}"


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, read in 1 MiB blocks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _tables_in(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Names of the tables in the connection's own database."""
    rows = con.execute(
        "SELECT table_name FROM duckdb_tables() "
        "WHERE database_name = current_database()"
    ).fetchall()
    return {r[0] for r in rows}


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def _count_sql(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0]) if row else 0


def check_source(db_path: Path) -> None:
    """Fail early unless the catalogue exists and holds the search index.

    Raises:
        ExportError: no catalogue, or a required table is missing or empty.
    """
    if not db_path.is_file():
        raise ExportError(f"no catalogue at {db_path}; run the pipeline first")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        present = _tables_in(con)
        missing = [t for t in REQUIRED_TABLES if t not in present or not _count(con, t)]
    finally:
        con.close()
    if missing:
        raise ExportError(
            f"the catalogue has no {', '.join(missing)}: run `chatcde index chunk`, "
            "`chatcde index embed` and `chatcde index bm25` first"
        )


def copy_tables(
    src_path: Path, out_path: Path, edition: Edition
) -> dict[str, tuple[int, int]]:
    """Copy :data:`BUNDLE_TABLES` from the catalogue into a new DuckDB file.

    Each table is created from the source's own ``CREATE TABLE`` statement, so
    column types and constraints survive, then filled with ``INSERT … SELECT``
    (filtered by :data:`OPEN_FILTERS` for the open edition). Tables the source
    lacks (e.g. ``parse_state`` in the test fixtures) are skipped with a log line.

    Returns:
        table -> (rows in the source, rows copied).
    """
    con = duckdb.connect(str(out_path))
    counts: dict[str, tuple[int, int]] = {}
    try:
        con.execute(f"ATTACH '{src_path}' AS src (READ_ONLY)")
        ddl = dict(
            con.execute(
                "SELECT table_name, sql FROM duckdb_tables() "
                "WHERE database_name = 'src'"
            ).fetchall()
        )
        for table in BUNDLE_TABLES:
            if table not in ddl:
                log.info("export: the catalogue has no %s table; skipped", table)
                continue
            con.execute(ddl[table])
            where = OPEN_FILTERS.get(table) if edition is Edition.OPEN else None
            sql = f"INSERT INTO main.{table} SELECT * FROM src.{table}"
            con.execute(sql + (f" WHERE {where}" if where else ""))
            counts[table] = (_count(con, f"src.{table}"), _count(con, f"main.{table}"))
            log.info(
                "export %s: %s %d of %d rows",
                edition.value,
                table,
                counts[table][1],
                counts[table][0],
            )
        con.execute("DETACH src")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return counts


def check_open_invariant(db_path: Path) -> None:
    """Assert what the open edition promises; raise :class:`ExportError` if not.

    No record without ``access = 'open'`` has pages or full-text chunks, and no
    vector, BM25 document or posting points to a chunk that is not there.
    """
    restricted = "r.access IS DISTINCT FROM 'open'"
    orphans = "chunk_id NOT IN (SELECT chunk_id FROM chunks)"
    # (what is counted, tables it needs, the count query)
    checks = [
        (
            "pages of non-open records",
            {"pages"},
            "SELECT count(*) FROM pages JOIN records r USING (record_id) "
            f"WHERE {restricted}",
        ),
        (
            "full-text chunks of non-open records",
            {"chunks"},
            "SELECT count(*) FROM chunks c JOIN records r USING (record_id) "
            f"WHERE c.source = '{TextSource.FULLTEXT.value}' AND {restricted}",
        ),
        (
            "vectors without a chunk",
            {"embeddings", "chunks"},
            f"SELECT count(*) FROM embeddings WHERE {orphans}",
        ),
        (
            "BM25 documents without a chunk",
            {"bm25_docs", "chunks"},
            f"SELECT count(*) FROM bm25_docs WHERE {orphans}",
        ),
        (
            "postings without a chunk",
            {"bm25_postings", "chunks"},
            f"SELECT count(*) FROM bm25_postings WHERE {orphans}",
        ),
    ]
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        present = _tables_in(con)
        failed = []
        for label, needed, sql in checks:
            if needed <= present and (n := _count_sql(con, sql)):
                failed.append(f"{n} {label}")
    finally:
        con.close()
    if failed:
        raise ExportError("open edition check failed: " + "; ".join(failed))


def _write_archive(archive: Path, members: list[Path]) -> None:
    """Pack ``members`` flat into ``archive`` (tar.gz), via a temp file and rename."""
    tmp = archive.with_name(archive.name + ".tmp")
    with tarfile.open(tmp, "w:gz", compresslevel=GZIP_LEVEL) as tar:
        for path in members:
            tar.add(path, arcname=path.name)
    os.replace(tmp, archive)


def build_bundle(
    cfg: Config,
    edition: Edition,
    version: str,
    out_dir: Path,
    *,
    force: bool = False,
) -> BundleResult:
    """Build one edition's bundle from ``cfg.data.catalogue`` into ``out_dir``.

    Skips the work if that edition and version are already built, unless
    ``force``. Writes ``<name>.tar.gz``, ``<name>.manifest.json`` and
    ``<name>.tar.gz.sha256``, where ``<name>`` is :func:`bundle_name`.

    Raises:
        ExportError: no catalogue or index, or the open-edition check failed.
    """
    name = bundle_name(edition, version)
    archive = out_dir / f"{name}.tar.gz"
    sidecar = out_dir / f"{name}.manifest.json"
    if archive.is_file() and sidecar.is_file() and not force:
        log.info("export: %s exists; skipped (use --force to rebuild)", archive)
        manifest = BundleManifest.model_validate_json(sidecar.read_text("utf-8"))
        return BundleResult(edition, archive, manifest, skipped=True)

    check_source(cfg.data.catalogue)
    staging = out_dir / f".{name}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        db_out = staging / CATALOGUE_NAME
        tables = copy_tables(cfg.data.catalogue, db_out, edition)
        if edition is Edition.OPEN:
            check_open_invariant(db_out)
        shutil.copyfile(cfg.vocab.file, staging / VOCABULARY_NAME)

        con = duckdb.connect(str(db_out), read_only=True)
        try:
            report = coverage.build_report(con, None, edition)
        finally:
            con.close()
        files = {p: sha256_file(staging / p) for p in (CATALOGUE_NAME, VOCABULARY_NAME)}
        manifest = BundleManifest(
            edition=edition,
            version=version,
            built_at=now(),
            code_version=code_version(),
            vocab_version=cfg.vocab.version,
            models=cfg.models.model_dump(),
            record_count=report.total_records,
            fulltext_count=report.with_fulltext,
            files=files,
        )
        manifest_json = manifest.model_dump(mode="json")
        write_json(staging / MANIFEST_NAME, manifest_json)
        members = (MANIFEST_NAME, CATALOGUE_NAME, VOCABULARY_NAME)
        _write_archive(archive, [staging / m for m in members])
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    write_json(sidecar, manifest_json)
    (out_dir / f"{archive.name}.sha256").write_text(
        f"{sha256_file(archive)}  {archive.name}\n", encoding="utf-8"
    )
    result = BundleResult(
        edition,
        archive,
        manifest,
        tables=tables,
        chunks=tables.get("chunks", (0, 0))[1],
    )
    log.info(
        "export %s %s: %d records, %d with full text, %d chunks, %.1f MB",
        edition.value,
        version,
        manifest.record_count,
        manifest.fulltext_count,
        result.chunks,
        result.archive_bytes / 1e6,
    )
    return result
