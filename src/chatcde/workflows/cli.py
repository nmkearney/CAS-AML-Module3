"""Assistant commands: free questions and fixed workflows.

``evaluate_questions`` is not registered on :data:`app`; ``chatcde.cli``
places it in the ``evaluate`` group.
"""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_ASSISTANT, not_implemented

app = typer.Typer()


@app.command(rich_help_panel=PANEL_ASSISTANT)
def ask(
    question: Annotated[str, typer.Argument(help="Your question.")],
) -> None:
    """Ask one question; the agent calls tools and answers with citations."""
    not_implemented("ask", 26)


@app.command(rich_help_panel=PANEL_ASSISTANT)
def chat() -> None:
    """Start an interactive conversation with the agent."""
    not_implemented("chat", 26)


@app.command(rich_help_panel=PANEL_ASSISTANT)
def gaps() -> None:
    """Find themes, methods, regions or actors CDE has rarely covered."""
    not_implemented("gaps", 27)


@app.command(rich_help_panel=PANEL_ASSISTANT)
def frontier() -> None:
    """Suggest directions that build on CDE's strengths."""
    not_implemented("frontier")


@app.command(rich_help_panel=PANEL_ASSISTANT)
def brief() -> None:
    """Write a short literature brief on a topic from CDE's work."""
    not_implemented("brief")


@app.command(rich_help_panel=PANEL_ASSISTANT)
def experts() -> None:
    """Find CDE researchers who have worked on a topic."""
    not_implemented("experts")


def evaluate_questions() -> None:
    """Run the test questions in tests/questions.yaml and score the answers."""
    not_implemented("evaluate questions", 28)
