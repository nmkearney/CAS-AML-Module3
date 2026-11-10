from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from chatcde import __version__
from chatcde.cli import app

runner = CliRunner()

TOP_LEVEL = [
    "ask", "chat", "gaps", "frontier", "brief", "experts",
    "search", "show", "read", "count", "schema", "coverage", "vocab",
    "harvest", "parse", "tag", "index", "evaluate",
    "export", "fetch-data", "info", "config",
]  # fmt: skip


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in TOP_LEVEL:
        assert name in result.output, name


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["evaluate", "tagging"],
        ["fetch-data", "--top-up"],
    ],
)
def test_stubs_exit_1_and_say_so(args: list[str]):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_config_show_reflects_explicit_override(tmp_path: Path):
    override = tmp_path / "o.yaml"
    override.write_text("models:\n  agent: test-agent\nlogging:\n  file: null\n")
    result = runner.invoke(app, ["--config", str(override), "config", "show"])
    assert result.exit_code == 0
    assert "agent: test-agent" in result.output


def test_config_path_lists_files_in_order(tmp_path: Path):
    override = tmp_path / "o.yaml"
    override.write_text("{}\n")
    result = runner.invoke(app, ["-c", str(override), "config", "path"])
    lines = result.output.strip().splitlines()
    assert lines[0].endswith("config/default.yaml")
    assert lines[-1] == str(override)


def test_invalid_config_exits_2_with_message(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("retrieval:\n  topn: 3\n")
    result = runner.invoke(app, ["-c", str(bad), "harvest"])
    assert result.exit_code == 2
    assert "topn" in result.output


def test_verbose_and_quiet_conflict():
    result = runner.invoke(app, ["-v", "-q", "harvest"])
    assert result.exit_code != 0
