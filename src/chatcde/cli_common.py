"""Helpers shared by the sub-apps in each subpackage's ``cli.py``."""

from __future__ import annotations

from typing import NoReturn

import typer

# Panels that group commands in ``chatcde --help``.
PANEL_ASSISTANT = "Assistant (uses the local LLM)"
PANEL_QUERY = "Query the catalogue (no LLM)"
PANEL_PIPELINE = "Pipeline stages"
PANEL_DATA = "Data bundles"
PANEL_SETUP = "Setup"


def not_implemented(command: str, issue: int | None = None) -> NoReturn:
    """Tell the user a command is a placeholder and exit with status 1.

    A non-zero exit keeps a stub from passing for success in scripts.

    Args:
        command: The command as typed, e.g. ``"harvest"``.
        issue: GitHub issue that implements it, if there is one.
    """
    where = f" (see issue #{issue})" if issue else ""
    typer.echo(f"`chatcde {command}` is not implemented yet{where}.", err=True)
    raise typer.Exit(code=1)
