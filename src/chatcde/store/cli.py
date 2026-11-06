"""``chatcde index``: build chunks, embeddings and the search index."""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE, not_implemented

app = typer.Typer()


@app.command(rich_help_panel=PANEL_PIPELINE)
def index(
    force: Annotated[
        bool, typer.Option(help="Redo work that is already done.")
    ] = False,
) -> None:
    """Chunk the texts, embed them and build the hybrid search index."""
    not_implemented("index", 21)
