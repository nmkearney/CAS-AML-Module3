"""Shared test setup: keep the developer's own config and logs out of every test."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user config at an empty temp folder and drop ``CHATCDE_CONFIG``.

    Also writes an explicit override that turns off the log file, so tests never
    write to ``data/logs``. Returns the folder used as ``XDG_CONFIG_HOME``.
    """
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    override = tmp_path / "test-config.yaml"
    override.write_text("logging:\n  file: null\n", encoding="utf-8")
    monkeypatch.setenv("CHATCDE_CONFIG", str(override))
    return xdg


# --- Test fixtures (tests/fixtures/, issue #3) --------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the fixture catalogue once per test session and return its path.

    Tables: records, authors, tags, pages, downloads (see
    ``tests/fixtures/build_fixture_db.py``). Open it read-only, e.g.
    ``duckdb.connect(str(fixture_db), read_only=True)``.
    """
    import build_fixture_db  # tests/fixtures/ is put on sys.path below

    return build_fixture_db.build(
        FIXTURES_DIR, tmp_path_factory.mktemp("fixture_db") / "fixture.duckdb"
    )


sys.path.insert(0, str(FIXTURES_DIR))
