"""Shared test setup: keep the developer's own config and logs out of every test."""

from __future__ import annotations

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
