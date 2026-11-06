"""``chatcde harvest …``: fetch CDE records and files from BORIS into the catalogue.

Subcommands:

* ``metadata``: BORIS search → ``records`` and ``authors`` tables (issue #7).
* ``files``: PDFs with a download manifest (issue #8, not yet implemented).
* ``openalex``: abstracts for DOIs without one (issue #9, not yet implemented).

Heavy modules (httpx, duckdb) are imported inside the commands so that
``chatcde --help`` stays fast (decision 0006).
"""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE, not_implemented

app = typer.Typer()
harvest_app = typer.Typer(
    help="Harvest CDE records from BORIS (UniBe network or VPN only).",
    no_args_is_help=True,
)
app.add_typer(harvest_app, name="harvest", rich_help_panel=PANEL_PIPELINE)

ForceOption = Annotated[
    bool, typer.Option("--force", help="Redo work that is already done.")
]


@harvest_app.command("metadata")
def harvest_metadata_cmd(
    ctx: typer.Context,
    force: ForceOption = False,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Rebuild the catalogue from the saved search pages; no network.",
        ),
    ] = False,
) -> None:
    """Fetch CDE records from the BORIS search and write the catalogue.

    Saves every search page under data/raw/boris/ (resumes if interrupted), then
    rebuilds the records and authors tables. Needs the UniBe VPN unless --offline.
    """
    from rich.console import Console
    from rich.table import Table

    from chatcde.harvest.boris import BorisError, NotOnVPNError
    from chatcde.harvest.metadata import HarvestError, harvest_metadata

    cfg = ctx.obj
    try:
        s = harvest_metadata(cfg, force=force, offline=offline)
    except NotOnVPNError as exc:
        typer.echo(f"Not on the UniBe network: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (BorisError, HarvestError) as exc:
        typer.echo(f"Harvest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    t = Table(title="chatcde harvest metadata", show_header=False)
    t.add_column(style="bold")
    t.add_column(justify="right")
    t.add_row("Search hits (unique items)", str(s.search_hits))
    t.add_row("Items naming CDE", str(s.cde_items))
    for etype, n in s.cde_by_entity_type.items():
        t.add_row(f"  {etype}", str(n))
    t.add_row("Publications written", str(s.records))
    for link, n in s.by_cde_link.items():
        t.add_row(f"  CDE link: {link}", str(n))
    t.add_row("  with a DOI", str(s.with_doi))
    t.add_row("  with an abstract", str(s.with_abstract))
    t.add_row("  with a CDE unit", str(s.with_cde_unit))
    t.add_row("Authors written", str(s.authors))
    for reason, n in s.dropped.items():
        t.add_row(f"Left out: {reason}", str(n))
    if s.removed:
        t.add_row("Removed (no longer in BORIS)", str(s.removed))
    Console().print(t)
    typer.echo(f"Catalogue: {cfg.data.catalogue}")


@harvest_app.command("files")
def harvest_files_cmd(force: ForceOption = False) -> None:
    """Download PDFs from BORIS with a download manifest (UniBe VPN only)."""
    not_implemented("harvest files", 8)


@harvest_app.command("openalex")
def harvest_openalex_cmd(force: ForceOption = False) -> None:
    """Fill missing abstracts from OpenAlex for records with a DOI."""
    not_implemented("harvest openalex", 9)
