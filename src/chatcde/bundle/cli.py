"""``chatcde export``, ``fetch-data`` and ``info``: the data bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from chatcde.cli_common import PANEL_DATA, not_implemented
from chatcde.schemas import Edition

app = typer.Typer()


@app.command(rich_help_panel=PANEL_DATA)
def export(
    ctx: typer.Context,
    edition: Annotated[
        Edition | None,
        typer.Option(help="Build only this edition; default: both."),
    ] = None,
    version: Annotated[
        str | None,
        typer.Option("--version", help="Bundle version; default: today, YYYY.MM.DD."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Output folder; default: data.bundles_dir in the config."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Rebuild a bundle that already exists.")
    ] = False,
) -> None:
    """Build a distributable data bundle from the local index.

    Each edition becomes chatcde-<edition>-<version>.tar.gz (catalogue, search
    index, vocabulary, manifest.json) plus a manifest copy and a .sha256 file.
    The open edition keeps full text only for open-access records (decision 0032).
    Needs the search index: run `chatcde index chunk`, `embed` and `bm25` first.
    """
    from datetime import date

    from rich.console import Console
    from rich.table import Table

    from chatcde.bundle.export import ExportError, build_bundle

    cfg = ctx.obj
    editions = [edition] if edition else [Edition.FULL, Edition.OPEN]
    version = version or date.today().strftime("%Y.%m.%d")
    out_dir = out or cfg.data.bundles_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    t = Table(title=f"chatcde export {version}")
    numeric = {"records", "full text", "chunks", "MB"}
    for col in ("edition", "status", "records", "full text", "chunks", "MB", "archive"):
        t.add_column(col, justify="right" if col in numeric else "left")
    for ed in editions:
        try:
            r = build_bundle(cfg, ed, version, out_dir, force=force)
        except ExportError as exc:
            typer.echo(f"export {ed.value}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        t.add_row(
            ed.value,
            "skipped" if r.skipped else "ok",
            f"{r.manifest.record_count:,}",
            f"{r.manifest.fulltext_count:,}",
            f"{r.chunks:,}" if not r.skipped else "",
            f"{r.archive_bytes / 1e6:,.1f}",
            str(r.archive),
        )
    Console().print(t)


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
