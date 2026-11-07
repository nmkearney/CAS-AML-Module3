"""``chatcde harvest …``: fetch CDE records and files from BORIS into the catalogue.

Subcommands:

* ``metadata``: BORIS search → ``records`` and ``authors`` tables (issue #7).
* ``files``: each record's main PDF with a download manifest (issue #8).
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
def harvest_files_cmd(
    ctx: typer.Context,
    force: ForceOption = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Try at most this many records."),
    ] = None,
) -> None:
    """Download each record's main PDF from BORIS with a download manifest.

    Files go to data/raw/files/<record_id>.pdf; one row per record goes to the
    downloads table (downloaded / restricted / no_file / error). Resumes where
    it stopped; finished records are skipped unless --force. Needs the UniBe VPN.
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

    from chatcde.harvest.boris import BorisError, NotOnVPNError
    from chatcde.harvest.files import download_files
    from chatcde.harvest.metadata import HarvestError
    from chatcde.schemas import ManifestEntry

    cfg = ctx.obj
    err = Console(stderr=True)
    with Progress(
        TextColumn("[bold]files"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        TextColumn("{task.description}"),
        console=err,
        transient=True,
    ) as bar:
        task = bar.add_task("", total=None)

        def step(done: int, total: int, entry: ManifestEntry) -> None:
            bar.update(
                task, completed=done, total=total, description=entry.status.value
            )

        try:
            s = download_files(cfg, force=force, limit=limit, progress=step)
        except NotOnVPNError as exc:
            typer.echo(f"Not on the UniBe network: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except (BorisError, HarvestError) as exc:
            typer.echo(f"Download failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    t = Table(title="chatcde harvest files", show_header=False)
    t.add_column(style="bold")
    t.add_column(justify="right")
    t.add_row("Records in the catalogue", str(s.records))
    t.add_row("Tried in this run", str(s.attempted))
    t.add_row("Finished earlier (skipped)", str(s.skipped))
    for status, n in s.by_status.items():
        t.add_row(f"Manifest: {status}", f"{n} / {s.records}")
    t.add_row("Downloaded size", f"{s.downloaded_bytes / 1e9:.2f} GB")
    Console().print(t)

    statuses = sorted({st for row in s.by_access.values() for st in row})
    x = Table(title="BORIS access (rows) x download status (columns)")
    x.add_column("access", style="bold")
    for st in [*statuses, "total"]:
        x.add_column(st, justify="right")
    for access, row in s.by_access.items():
        cells = [str(row.get(st, 0)) for st in statuses]
        x.add_row(access, *cells, str(sum(row.values())))
    Console().print(x)
    typer.echo(f"Files: {cfg.data.files_dir}")


@harvest_app.command("openalex")
def harvest_openalex_cmd(force: ForceOption = False) -> None:
    """Fill missing abstracts from OpenAlex for records with a DOI."""
    not_implemented("harvest openalex", 9)
