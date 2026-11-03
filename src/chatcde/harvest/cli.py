"""``chatcde harvest``: fetch CDE records from BORIS into the catalogue."""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE, not_implemented

app = typer.Typer()


@app.command(rich_help_panel=PANEL_PIPELINE)
def harvest(
    force: Annotated[
        bool, typer.Option(help="Redo work that is already done.")
    ] = False,
) -> None:
    """Harvest CDE records from BORIS (UniBe network or VPN only)."""
    not_implemented("harvest", 7)
