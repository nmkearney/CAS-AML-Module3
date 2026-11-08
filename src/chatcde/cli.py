"""The ``chatcde`` command: assembles the sub-apps and sets up config and logging.

Each subpackage defines its commands in its own ``cli.py`` (``app``); this module
only merges them into one command and adds the global options. Sub-app modules
import heavy libraries inside their command functions, so ``chatcde --help``
stays fast. See docs/decisions/0006-config-logging-cli-assembly.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
import yaml

from chatcde import __version__
from chatcde.bundle import cli as bundle_cli
from chatcde.cli_common import PANEL_PIPELINE, PANEL_QUERY, PANEL_SETUP
from chatcde.config import ConfigError, find_config_files, load_config
from chatcde.harvest import cli as harvest_cli
from chatcde.log import setup_logging
from chatcde.parse import cli as parse_cli
from chatcde.store import cli as store_cli
from chatcde.tag import cli as tag_cli
from chatcde.tools import cli as tools_cli
from chatcde.workflows import cli as workflows_cli

log = logging.getLogger(__name__)

app = typer.Typer(
    help="ChatCDE: ask a local LLM about CDE's publications (prototype).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,  # never print config values or keys
)

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Extra config file, merged last. Default: $CHATCDE_CONFIG.",
        dir_okay=False,
    ),
]


def _version_callback(value: bool) -> None:
    """Print the package version and exit (eager ``--version`` option)."""
    if value:
        typer.echo(f"chatcde {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    config: ConfigOption = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show debug messages.")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Show warnings and errors only.")
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show version."
        ),
    ] = False,
) -> None:
    """Load the config, set up logging and pass the config to the command.

    The ``config`` commands load the config themselves, so they can report a
    broken config file instead of failing before they run.
    """
    if verbose and quiet:
        raise typer.BadParameter("use --verbose or --quiet, not both")
    if ctx.invoked_subcommand == "config":
        setup_logging("DEBUG" if verbose else "INFO")
        ctx.obj = config
        return
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    level = "DEBUG" if verbose else "WARNING" if quiet else cfg.logging.level
    setup_logging(
        level,
        file=cfg.logging.file,
        file_level=cfg.logging.file_level,
        max_bytes=cfg.logging.max_bytes,
        backup_count=cfg.logging.backup_count,
    )
    log.debug("chatcde %s, command %s", __version__, ctx.invoked_subcommand)
    ctx.obj = cfg


# --- chatcde config ----------------------------------------------------------

config_app = typer.Typer(help="Show the merged configuration and where it came from.")


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Print the merged configuration as YAML (secrets are never stored in it)."""
    try:
        cfg = load_config(ctx.obj)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False).rstrip())


@config_app.command("path")
def config_path(ctx: typer.Context) -> None:
    """List the config files that are merged, lowest priority first."""
    try:
        files = find_config_files(ctx.obj)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    for path in files:
        typer.echo(str(path))


# --- Assembly ------------------------------------------------------------------

vocab_app = typer.Typer(help="List vocabulary terms, or draft new ones.")
vocab_app.callback(invoke_without_command=True)(tools_cli.vocab_list)
vocab_app.command("extract")(tag_cli.vocab_extract)
vocab_app.command("review")(tag_cli.vocab_review)
vocab_app.command("apply")(tag_cli.vocab_apply)

evaluate_app = typer.Typer(help="Evaluate tagging or the assistant's answers.")
evaluate_app.command("tagging")(tag_cli.evaluate_tagging)
evaluate_app.command("review-sheet")(tag_cli.review_sheet)
evaluate_app.command("questions")(workflows_cli.evaluate_questions)

for sub_app in (
    workflows_cli.app,
    tools_cli.app,
    harvest_cli.app,
    parse_cli.app,
    tag_cli.app,
    store_cli.app,
    bundle_cli.app,
):
    app.add_typer(sub_app)
app.add_typer(vocab_app, name="vocab", rich_help_panel=PANEL_QUERY)
app.add_typer(evaluate_app, name="evaluate", rich_help_panel=PANEL_PIPELINE)
app.add_typer(config_app, name="config", rich_help_panel=PANEL_SETUP)


def main() -> None:
    """Entry point for the ``chatcde`` console script."""
    app()
