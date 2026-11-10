"""Tests for ``chatcde export`` (issue #17, decision 0032). Local only, no endpoint."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from chatcde.bundle.export import (
    CATALOGUE_NAME,
    MANIFEST_NAME,
    VOCABULARY_NAME,
    ExportError,
    build_bundle,
    bundle_name,
    sha256_file,
)
from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.schemas import BundleManifest, Edition
from chatcde.store.bm25 import build_bm25
from chatcde.store.chunking import ChunkSettings
from chatcde.store.embeddings import EmbedSettings, build_embeddings
from chatcde.store.index import build_chunks
from test_store_search import FakeEndpoint, make_client

CHUNKING = ChunkSettings(chunk_tokens=500, overlap_tokens=50, tokens_per_word=1.4)
VERSION = "2026.11.10"


def _one(db: Path, sql: str, params: list[object] | None = None) -> object:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql, params or []).fetchone()[0]  # type: ignore[index]
    finally:
        con.close()


@pytest.fixture(scope="module")
def indexed_db(fixture_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fixture catalogue with chunks, fake vectors and BM25 (do not write to it)."""
    db = Path(shutil.copy(fixture_db, tmp_path_factory.mktemp("x") / "c.duckdb"))
    build_chunks(db, CHUNKING)
    build_embeddings(
        db, make_client(FakeEndpoint()), EmbedSettings(model="fake", batch_size=64)
    )
    build_bm25(db)
    return db


@pytest.fixture
def source(indexed_db: Path, tmp_path: Path) -> tuple[Path, str]:
    """A writable copy with one record that has pages and an abstract set restricted.

    Returns the catalogue path and the restricted record's ID.
    """
    db = Path(shutil.copy(indexed_db, tmp_path / "catalogue.duckdb"))
    con = duckdb.connect(str(db))
    try:
        has = "record_id IN (SELECT record_id FROM chunks WHERE source {} 'fulltext')"
        rid = con.execute(
            "SELECT record_id FROM records WHERE access = 'open' "
            f"AND {has.format('=')} AND {has.format('<>')} "
            "ORDER BY record_id LIMIT 1"
        ).fetchone()[0]  # type: ignore[index]
        con.execute(
            "UPDATE records SET access = 'restricted' WHERE record_id = ?", [rid]
        )
    finally:
        con.close()
    return db, rid


def _config(tmp_path: Path, db: Path) -> Config:
    override = tmp_path / "export.yaml"
    override.write_text(
        f'data:\n  dir: "{tmp_path.as_posix()}"\n  catalogue: "{db.as_posix()}"\n'
        f'  bundles_dir: "{(tmp_path / "bundles").as_posix()}"\n'
        "logging:\n  file: null\n",
        encoding="utf-8",
    )
    return load_config(override)


def _unpack(archive: Path, dest: Path) -> Path:
    with tarfile.open(archive) as tar:
        assert sorted(tar.getnames()) == sorted(
            [MANIFEST_NAME, CATALOGUE_NAME, VOCABULARY_NAME]
        )
        tar.extractall(dest, filter="data")
    return dest


def test_open_edition_drops_restricted_full_text(source, tmp_path: Path):
    db, rid = source
    cfg = _config(tmp_path, db)
    r = build_bundle(cfg, Edition.OPEN, VERSION, tmp_path / "bundles")
    assert not r.skipped
    out = _unpack(r.archive, tmp_path / "open") / CATALOGUE_NAME

    q = "SELECT count(*) FROM {} WHERE record_id = ?"
    assert _one(out, q.format("pages"), [rid]) == 0
    assert _one(out, q.format("chunks") + " AND source = 'fulltext'", [rid]) == 0
    assert _one(out, q.format("chunks") + " AND source <> 'fulltext'", [rid]) > 0
    assert _one(out, q.format("records"), [rid]) == 1
    assert _one(out, q.format("tags"), [rid]) == _one(db, q.format("tags"), [rid])
    for table in ("embeddings", "bm25_docs", "bm25_postings"):
        orphans = f"SELECT count(*) FROM {table} WHERE chunk_id NOT IN "
        assert _one(out, orphans + "(SELECT chunk_id FROM chunks)") == 0, table
    assert _one(out, "SELECT count(*) FROM embeddings") == _one(
        out, "SELECT count(*) FROM chunks"
    )
    assert r.tables["pages"][1] < r.tables["pages"][0]
    assert r.manifest.edition is Edition.OPEN


def test_full_edition_copies_everything(source, tmp_path: Path):
    db, rid = source
    cfg = _config(tmp_path, db)
    r = build_bundle(cfg, Edition.FULL, VERSION, tmp_path / "bundles")
    out = _unpack(r.archive, tmp_path / "full") / CATALOGUE_NAME
    for table, (n_src, n_out) in r.tables.items():
        assert n_src == n_out, table
    assert _one(out, "SELECT count(*) FROM pages WHERE record_id = ?", [rid]) > 0
    assert set(r.tables) >= {"records", "chunks", "embeddings", "bm25_postings"}
    assert "tag_runs" not in r.tables


def test_manifest_checksums_and_sidecars(source, tmp_path: Path):
    db, _ = source
    cfg = _config(tmp_path, db)
    out_dir = tmp_path / "bundles"
    r = build_bundle(cfg, Edition.OPEN, VERSION, out_dir)
    dest = _unpack(r.archive, tmp_path / "u")
    inner = BundleManifest.model_validate_json((dest / MANIFEST_NAME).read_text())
    assert set(inner.files) == {CATALOGUE_NAME, VOCABULARY_NAME}
    for name, digest in inner.files.items():
        assert sha256_file(dest / name) == digest
    assert inner.vocab_version == cfg.vocab.version
    assert inner.models["embedding"] == cfg.models.embedding
    assert inner.record_count == _one(db, "SELECT count(*) FROM records")

    name = bundle_name(Edition.OPEN, VERSION)
    sidecar = json.loads((out_dir / f"{name}.manifest.json").read_text())
    assert sidecar == json.loads((dest / MANIFEST_NAME).read_text())
    digest, fname = (out_dir / f"{name}.tar.gz.sha256").read_text().split()
    assert fname == r.archive.name and digest == sha256_file(r.archive)
    assert not list(out_dir.glob(".*.tmp")) and not list(out_dir.glob("*.tmp"))


def test_rerun_skips_unless_force(source, tmp_path: Path):
    db, _ = source
    cfg = _config(tmp_path, db)
    out_dir = tmp_path / "bundles"
    first = build_bundle(cfg, Edition.FULL, VERSION, out_dir)
    again = build_bundle(cfg, Edition.FULL, VERSION, out_dir)
    assert again.skipped and again.manifest == first.manifest
    forced = build_bundle(cfg, Edition.FULL, VERSION, out_dir, force=True)
    assert not forced.skipped


def test_missing_index_is_an_error(fixture_db: Path, tmp_path: Path):
    db = Path(shutil.copy(fixture_db, tmp_path / "noindex.duckdb"))
    cfg = _config(tmp_path, db)
    with pytest.raises(ExportError, match="chatcde index"):
        build_bundle(cfg, Edition.OPEN, VERSION, tmp_path / "bundles")


def test_cli_builds_both_editions(source, tmp_path: Path):
    db, _ = source
    _config(tmp_path, db)
    runner = CliRunner()
    result = runner.invoke(
        app, ["-c", str(tmp_path / "export.yaml"), "export", "--version", VERSION]
    )
    assert result.exit_code == 0, result.output
    for ed in Edition:
        assert (tmp_path / "bundles" / f"{bundle_name(ed, VERSION)}.tar.gz").is_file()
