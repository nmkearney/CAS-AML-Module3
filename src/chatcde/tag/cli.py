"""``chatcde tag``, plus ``vocab extract`` and ``evaluate tagging``.

``vocab_extract`` and ``evaluate_tagging`` are not registered on :data:`app`;
``chatcde.cli`` places them in the ``vocab`` and ``evaluate`` groups.
"""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE, not_implemented

app = typer.Typer()


@app.command(rich_help_panel=PANEL_PIPELINE)
def tag(
    force: Annotated[
        bool, typer.Option(help="Redo work that is already done.")
    ] = False,
) -> None:
    """Tag records against the vocabulary with the local LLM."""
    not_implemented("tag", 15)


def vocab_extract() -> None:
    """Open extraction on a stratified sample, for drafting the vocabulary."""
    not_implemented("vocab extract", 13)


def evaluate_tagging() -> None:
    """Score LLM tags against the hand-tagged reference set."""
    not_implemented("evaluate tagging", 16)
