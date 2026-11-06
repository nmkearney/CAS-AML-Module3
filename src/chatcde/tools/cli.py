"""Direct tool calls without the LLM: ``search``, ``count``, ``schema``, ...

Thin wrappers around :mod:`chatcde.tools`. ``vocab_list`` is not registered on
:data:`app`; ``chatcde.cli`` uses it as the ``vocab`` group's default action.
"""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_QUERY, not_implemented

app = typer.Typer()


@app.command(rich_help_panel=PANEL_QUERY)
def search(
    query: Annotated[str, typer.Argument(help="Search text, in any language.")],
) -> None:
    """Search publications and show matching passages with BORIS IDs."""
    not_implemented("search", 23)


@app.command(rich_help_panel=PANEL_QUERY)
def count() -> None:
    """Count publications by one or two schema dimensions."""
    not_implemented("count", 24)


@app.command(rich_help_panel=PANEL_QUERY)
def schema() -> None:
    """Show the schema dimensions and their scope notes."""
    not_implemented("schema", 24)


@app.command(rich_help_panel=PANEL_QUERY)
def coverage() -> None:
    """Report how many records have metadata, abstracts, full text and tags."""
    not_implemented("coverage", 11)


def vocab_list(ctx: typer.Context) -> None:
    """List vocabulary terms; ``vocab extract`` drafts new ones."""
    if ctx.invoked_subcommand is None:
        not_implemented("vocab", 24)
