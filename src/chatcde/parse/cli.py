"""``chatcde parse``: turn downloaded PDFs into page text (issue #10, decision 0014).

Heavy modules (PyMuPDF, lingua, duckdb) are imported inside the command so that
``chatcde --help`` stays fast (decision 0006).
"""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE

app = typer.Typer()


@app.command(rich_help_panel=PANEL_PIPELINE)
def parse(
    ctx: typer.Context,
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Read every PDF again (cached OCR answers are reused)."
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Read at most this many PDFs."),
    ] = None,
    record: Annotated[
        list[str] | None,
        typer.Option("--record", help="Only this record ID (repeatable)."),
    ] = None,
    no_ocr: Annotated[
        bool,
        typer.Option(
            "--no-ocr",
            help="Do not call the vision model; pages needing OCR stay pending.",
        ),
    ] = False,
) -> None:
    """Extract page text from PDFs, with OCR for scanned pages.

    Reads data/raw/files/<record_id>.pdf for every downloaded record and writes
    one row per page to the pages table. Pages without a usable text layer go to
    the vision model (models.vision). Resumes where it stopped; finished records
    are skipped unless --force.
    """
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table

    from chatcde.parse.pages import ParseError, parse_pdfs

    cfg = ctx.obj
    err = Console(stderr=True)
    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=err,
        transient=True,
    ) as bar:
        tasks: dict[str, int] = {}

        def step(name: str, done: int, total: int) -> None:
            if name not in tasks:
                label = "text layer" if name == "text" else "OCR pages"
                tasks[name] = bar.add_task(label, total=total)
            bar.update(tasks[name], completed=done, total=total)

        try:
            s = parse_pdfs(
                cfg,
                force=force,
                limit=limit,
                record_ids=record or None,
                ocr=not no_ocr,
                progress=step,
            )
        except ParseError as exc:
            typer.echo(f"Parse failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    t = Table(title="chatcde parse", show_header=False)
    t.add_column(style="bold")
    t.add_column(justify="right")
    t.add_row("Records with a PDF", str(s.records))
    t.add_row("Read in this run", str(s.parsed))
    t.add_row("Parsed earlier (skipped)", str(s.skipped))
    if s.removed:
        t.add_row("Removed (PDF no longer downloaded)", str(s.removed))
    if s.errors:
        t.add_row("Errors in this run", str(s.errors))
    for status, n in s.by_status.items():
        t.add_row(f"Records {status}", str(n))
    t.add_row("Pages", str(s.pages))
    for cls, n in s.by_class.items():
        t.add_row(f"  {cls}", str(n))
    t.add_row("Pages from OCR", str(s.ocr_pages))
    t.add_row("Pages waiting for OCR", str(s.ocr_pending))
    t.add_row("Vision requests (this run)", str(s.ocr_requests))
    t.add_row("  from cache", str(s.ocr_cached))
    t.add_row("  failed", str(s.ocr_failed))
    for lang, n in list(s.by_language.items())[:8]:
        t.add_row(f"Pages in {lang}", str(n))
    Console().print(t)
    if s.ocr_stopped:
        typer.echo(
            "OCR stopped after repeated failures; check the endpoint and run again.",
            err=True,
        )
        raise typer.Exit(code=1)
