"""``chatcde parse``: turn downloaded PDFs into page text."""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE, not_implemented

app = typer.Typer()


@app.command(rich_help_panel=PANEL_PIPELINE)
def parse(
    force: Annotated[
        bool, typer.Option(help="Redo work that is already done.")
    ] = False,
) -> None:
    """Extract page text from PDFs, with OCR for scanned pages."""
    not_implemented("parse", 10)
