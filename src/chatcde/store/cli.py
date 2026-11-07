"""``chatcde index``: build chunks, embeddings and the search index."""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE

app = typer.Typer()
index_app = typer.Typer(
    help="Build the search index: chunk the texts, then embed them.",
    no_args_is_help=True,
)
app.add_typer(index_app, name="index", rich_help_panel=PANEL_PIPELINE)

ForceOption = Annotated[
    bool, typer.Option("--force", help="Redo work that is already done.")
]


@index_app.command("chunk")
def index_chunk_cmd(
    ctx: typer.Context,
    force: ForceOption = False,
    limit: Annotated[
        int | None,
        typer.Option(min=1, help="Chunk at most this many records (for trying out)."),
    ] = None,
) -> None:
    """Split records and pages into ~500-token passages (the chunks table).

    Records with an abstract get abstract chunks, records with pages get full-text
    chunks, and records with neither get one chunk of title and keywords. Records
    whose text and settings have not changed are skipped unless --force.
    """
    from rich.console import Console
    from rich.progress import Progress
    from rich.table import Table

    from chatcde.store.chunking import ChunkSettings
    from chatcde.store.index import ChunkingError, build_chunks

    cfg = ctx.obj
    settings = ChunkSettings(
        chunk_tokens=cfg.retrieval.chunk_tokens,
        overlap_tokens=cfg.retrieval.chunk_overlap_tokens,
        tokens_per_word=cfg.retrieval.tokens_per_word,
    )
    try:
        with Progress(console=Console(stderr=True), transient=True) as bar:
            task = bar.add_task("Chunking", total=None)

            def step(done: int, total: int) -> None:
                bar.update(task, completed=done, total=total)

            s = build_chunks(
                cfg.data.catalogue, settings, force=force, limit=limit, progress=step
            )
    except ChunkingError as exc:
        typer.echo(f"Chunking failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    t = Table(title="chatcde index chunk", show_header=False)
    t.add_column(style="bold")
    t.add_column(justify="right")
    t.add_row("Records in the catalogue", str(s.records))
    t.add_row("Chunked in this run", str(s.chunked))
    t.add_row("Up to date (skipped)", str(s.skipped))
    if s.remaining:
        t.add_row("Left for a later run (--limit)", str(s.remaining))
    if s.removed:
        t.add_row("Removed (no longer in the catalogue)", str(s.removed))
    t.add_row("Chunks written", str(s.chunks_written))
    for source, n in s.by_source.items():
        t.add_row(f"  from {source}", str(n))
    if s.chunks_written:
        t.add_row("Tokens per chunk (mean / max)", f"{s.mean_tokens} / {s.max_tokens}")
    t.add_row("Chunks in the catalogue", str(s.chunks_total))
    Console().print(t)
    typer.echo(f"Catalogue: {cfg.data.catalogue}")


@index_app.command("embed")
def index_embed_cmd(
    ctx: typer.Context,
    force: ForceOption = False,
    limit: Annotated[
        int | None,
        typer.Option(min=1, help="Embed at most this many chunks (for trying out)."),
    ] = None,
    alternative: Annotated[
        bool,
        typer.Option(
            "--alternative",
            help="Use the config's alternative embedding model, for comparisons.",
        ),
    ] = False,
) -> None:
    """Embed the chunks with the embedding model (the vector index).

    Sends the chunks to the local endpoint in batches (up to llm.max_concurrency
    at a time). Chunks that already have a current vector are skipped unless
    --force. Needs the API key in the variable named by llm.api_key_env.
    """
    from rich.console import Console
    from rich.progress import Progress
    from rich.table import Table

    from chatcde.store.embeddings import EmbeddingError, EmbedSettings, build_embeddings
    from chatcde.store.endpoint import EndpointClient, EndpointError

    cfg = ctx.obj
    model = cfg.model_alternatives.embedding if alternative else cfg.models.embedding
    if not model:
        typer.echo("No alternative embedding model is set in the config.", err=True)
        raise typer.Exit(code=2)
    settings = EmbedSettings(
        model=model,
        batch_size=cfg.embedding.batch_size,
        dimensions=cfg.embedding.dimensions,
        include_title=cfg.embedding.include_title,
        max_concurrency=cfg.llm.max_concurrency,
    )
    try:
        with (
            EndpointClient.from_config(cfg) as client,
            Progress(console=Console(stderr=True), transient=True) as bar,
        ):
            task = bar.add_task(f"Embedding with {model}", total=None)

            def step(done: int, total: int) -> None:
                bar.update(task, completed=done, total=total)

            s = build_embeddings(
                cfg.data.catalogue,
                client,
                settings,
                force=force,
                limit=limit,
                progress=step,
            )
    except (EmbeddingError, EndpointError) as exc:
        typer.echo(f"Embedding failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    t = Table(title="chatcde index embed", show_header=False)
    t.add_column(style="bold")
    t.add_column(justify="right")
    t.add_row("Model", s.model)
    t.add_row("Dimensions", str(s.dims) if s.dims else "-")
    t.add_row("Chunks in the catalogue", str(s.chunks))
    t.add_row("Embedded in this run", str(s.embedded))
    t.add_row("Up to date (skipped)", str(s.skipped))
    if s.remaining:
        t.add_row("Left for a later run (--limit)", str(s.remaining))
    if s.removed:
        t.add_row("Removed (chunk changed or gone)", str(s.removed))
    t.add_row("Requests", f"{s.requests} in {s.seconds} s")
    t.add_row("Vectors for this model", str(s.vectors_total))
    Console().print(t)
    typer.echo(f"Catalogue: {cfg.data.catalogue}")
