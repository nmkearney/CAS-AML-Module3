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
def coverage(
    ctx: typer.Context,
    by: Annotated[
        list[str] | None,
        typer.Option(
            help="Breakdown to show: year, type, language, access (repeatable; "
            "default all)."
        ),
    ] = None,
    year_from: Annotated[int | None, typer.Option(help="Earliest year.")] = None,
    year_to: Annotated[int | None, typer.Option(help="Latest year.")] = None,
    unit: Annotated[
        list[str] | None, typer.Option(help="CDE unit (repeatable).")
    ] = None,
    pub_type: Annotated[
        list[str] | None, typer.Option("--type", help="BORIS type (repeatable).")
    ] = None,
    lang: Annotated[
        list[str] | None, typer.Option(help="Language code (repeatable).")
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the CoverageReport as JSON.")
    ] = False,
) -> None:
    """Report how many records have full text, only an abstract, or only metadata.

    Shares are shown by year, type, language and access rights, for the whole
    catalogue or for the records matching the filters. The numbers are the ones
    the assistant's `coverage_report` tool returns.
    """
    from rich.console import Console
    from rich.table import Table

    from chatcde import catalogue
    from chatcde.schemas import Filters
    from chatcde.store.filters import FilterError
    from chatcde.tools import coverage as cov

    cfg = ctx.obj
    keys = by or list(cov.BREAKDOWNS)
    unknown = [k for k in keys if k not in cov.BREAKDOWNS]
    if unknown:
        typer.echo(
            f"--by must be one of {', '.join(cov.BREAKDOWNS)}; "
            f"got {', '.join(unknown)}.",
            err=True,
        )
        raise typer.Exit(code=2)
    filters = Filters(
        year_from=year_from,
        year_to=year_to,
        cde_units=unit or [],
        pub_types=pub_type or [],
        languages=lang or [],
    )
    if not cfg.data.catalogue.is_file():
        typer.echo(
            f"No catalogue at {cfg.data.catalogue}; run `chatcde harvest metadata` "
            "or `chatcde fetch-data` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    con = catalogue.connect(cfg.data.catalogue, read_only=True)
    try:
        report = cov.build_report(con, filters, cov.local_edition(cfg.data.dir))
        basis = cov.fulltext_basis(con)
        splits = {k: cov.breakdown(con, filters, k) for k in keys}  # type: ignore[arg-type]
    except FilterError as exc:
        typer.echo(f"Coverage failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        con.close()

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        return

    def pct(n: int, d: int) -> str:
        return f"{100 * n / d:.0f}%" if d else "–"

    console = Console()
    n = report.total_records
    t = Table(title=f"chatcde coverage ({report.edition.value} edition)")
    t.add_column("Records")
    t.add_column("Count", justify="right")
    t.add_column("Share", justify="right")
    t.add_row("Total", str(n), "")
    for label, count in (
        ("  full text", report.with_fulltext),
        ("  abstract only", report.abstract_only),
        ("  metadata only", report.metadata_only),
    ):
        t.add_row(label, str(count), pct(count, n))
    t.add_row("Tags below min. confidence", str(report.low_confidence_tags), "")
    console.print(t)
    if basis == "downloads":
        console.print(
            "[yellow]No parsed pages yet: 'full text' means a downloaded PDF "
            "(run `chatcde parse`).[/yellow]"
        )
    for key, split in splits.items():
        bt = Table(title=f"By {key}")
        for col in (key, "Records", "Full text", "Abstract only", "Metadata only"):
            bt.add_column(col, justify="left" if col == key else "right")
        for value, c in split.items():
            total = sum(c.values())
            bt.add_row(
                value,
                str(total),
                f"{c['fulltext']} ({pct(c['fulltext'], total)})",
                f"{c['abstract_only']} ({pct(c['abstract_only'], total)})",
                f"{c['metadata_only']} ({pct(c['metadata_only'], total)})",
            )
        console.print(bt)


def vocab_list(ctx: typer.Context) -> None:
    """List vocabulary terms; ``vocab extract`` drafts new ones."""
    if ctx.invoked_subcommand is None:
        not_implemented("vocab", 24)
