"""Assistant commands: free questions and fixed workflows.

``evaluate_questions`` is not registered on :data:`app`; ``chatcde.cli``
places it in the ``evaluate`` group.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from chatcde.cli_common import PANEL_ASSISTANT, not_implemented

if TYPE_CHECKING:
    from chatcde.agent import Agent, AgentResult
    from chatcde.config import Config

app = typer.Typer()

AlternativeOption = Annotated[
    bool,
    typer.Option(
        "--alternative",
        help="Use the config's alternative agent model (model_alternatives.agent).",
    ),
]
LogOption = Annotated[
    Path | None,
    typer.Option(
        "--log",
        help="Append each answer, with its tool calls and provenance, as a JSON "
        "line to this file.",
        dir_okay=False,
    ),
]
ToolsOption = Annotated[
    bool,
    typer.Option("--show-tools/--hide-tools", help="Print each tool call as it runs."),
]


def _agent(cfg: Config, alternative: bool, show_tools: bool) -> Agent:
    """Build the agent for ``ask``/``chat``; exit 1/2 if the setup is incomplete."""
    from rich.console import Console

    from chatcde.agent import Agent
    from chatcde.workflows.answer import tool_line

    if not cfg.data.catalogue.is_file():
        typer.echo(
            f"No catalogue at {cfg.data.catalogue}; run `chatcde fetch-data` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    model = cfg.model_alternatives.agent if alternative else None
    if alternative and not model:
        typer.echo("No alternative agent model is set in the config.", err=True)
        raise typer.Exit(code=2)
    err = Console(stderr=True, highlight=False)
    return Agent(
        cfg,
        model=model,
        on_tool_call=(lambda r: err.print(f"[dim]{tool_line(r)}[/dim]"))
        if show_tools
        else None,
    )


def _ask_once(agent: Agent, question: str) -> AgentResult | None:
    """Run one question with a spinner; print endpoint errors and return None."""
    from rich.console import Console

    from chatcde.agent import AgentError
    from chatcde.store.endpoint import EndpointError

    err = Console(stderr=True)
    try:
        with err.status(f"Asking {agent.model} ...", spinner="dots"):
            return agent.ask(question)
    except (EndpointError, AgentError) as exc:
        err.print(f"[red]The agent failed: {exc}[/red]", highlight=False)
        return None


def _log(path: Path | None, result: AgentResult) -> None:
    """Append ``result`` as one JSON line to ``path`` (if given)."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(result.model_dump_json() + "\n")


@app.command(rich_help_panel=PANEL_ASSISTANT)
def ask(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument(help="Your question.")],
    alternative: AlternativeOption = False,
    show_tools: ToolsOption = True,
    log_file: LogOption = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full AgentResult as JSON.")
    ] = False,
) -> None:
    """Ask one question; the agent calls tools and answers with citations.

    The local agent model searches, reads and counts with ChatCDE's tools, then
    answers with numbered sources (BORIS and DOI links). A note says which data
    edition answered and how much of it has full text. Needs the endpoint key in
    the variable named by llm.api_key_env.
    """
    from rich.console import Console

    from chatcde.workflows.answer import data_note, render_result

    cfg = ctx.obj
    with _agent(cfg, alternative, show_tools) as agent:
        result = _ask_once(agent, question)
    if result is None:
        raise typer.Exit(code=1)
    _log(log_file, result)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    console = Console()
    render_result(console, result)
    note = data_note(cfg)
    if note:
        console.print(f"[dim]{note.text()}[/dim]", highlight=False)


@app.command(rich_help_panel=PANEL_ASSISTANT)
def chat(
    ctx: typer.Context,
    alternative: AlternativeOption = False,
    show_tools: ToolsOption = True,
    log_file: LogOption = None,
) -> None:
    """Start an interactive conversation with the agent.

    Each answer builds on the earlier questions. Type /reset to start a new
    conversation and /exit (or Ctrl-D) to leave.
    """
    from rich.console import Console

    from chatcde.workflows.answer import data_note, render_result

    cfg = ctx.obj
    console = Console()
    with _agent(cfg, alternative, show_tools) as agent:
        console.print(
            f"[bold]ChatCDE[/bold] with {agent.model}. Ask about CDE's "
            "publications; /reset starts over, /exit leaves.",
            highlight=False,
        )
        note = data_note(cfg)
        if note:
            console.print(f"[dim]{note.text()}[/dim]", highlight=False)
        while True:
            try:
                question = console.input("\n[bold cyan]You:[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            if not question:
                continue
            if question.lower() in ("/exit", "/quit"):
                break
            if question.lower() == "/reset":
                agent.reset()
                console.print("[dim]New conversation.[/dim]")
                continue
            result = _ask_once(agent, question)
            if result is None:
                continue
            _log(log_file, result)
            console.print()
            render_result(console, result)


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
