"""``chatcde export``, ``fetch-data`` and ``info``: the data bundles."""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_DATA, not_implemented
from chatcde.schemas import Edition

app = typer.Typer()


@app.command(rich_help_panel=PANEL_DATA)
def export(
    edition: Annotated[Edition, typer.Option(help="Which edition to build.")],
) -> None:
    """Build a distributable data bundle from the local index."""
    not_implemented("export", 17)


@app.command("fetch-data", rich_help_panel=PANEL_DATA)
def fetch_data(
    edition: Annotated[
        Edition | None,
        typer.Option(help="Force an edition; default: full on VPN, else open."),
    ] = None,
    top_up: Annotated[
        bool, typer.Option(help="Download remaining PDFs from BORIS yourself.")
    ] = False,
) -> None:
    """Download a prebuilt data bundle."""
    not_implemented("fetch-data", 18)


@app.command(rich_help_panel=PANEL_DATA)
def info() -> None:
    """Show the installed data edition, its versions and counts."""
    not_implemented("info", 18)
