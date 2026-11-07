"""``chatcde index``: build chunks, embeddings and the BM25 index; try a search."""

from __future__ import annotations

from typing import Annotated

import typer

from chatcde.cli_common import PANEL_PIPELINE

app = typer.Typer()
index_app = typer.Typer(
    help="Build the search index: chunk the texts, embed them, index keywords.",
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


@index_app.command("bm25")
def index_bm25_cmd(ctx: typer.Context, force: ForceOption = False) -> None:
    """Build the keyword (BM25) index over the chunks.

    Indexes the same title + chunk text the embedding model sees. Chunks that
    have not changed are skipped unless --force; chunks that are gone are
    removed. Runs locally, no endpoint needed.
    """
    from rich.console import Console
    from rich.progress import Progress
    from rich.table import Table

    from chatcde.store.bm25 import Bm25Error, build_bm25

    cfg = ctx.obj
    try:
        with Progress(console=Console(stderr=True), transient=True) as bar:
            task = bar.add_task("BM25 index", total=None)

            def step(done: int, total: int) -> None:
                bar.update(task, completed=done, total=total)

            s = build_bm25(
                cfg.data.catalogue,
                include_title=cfg.embedding.include_title,
                force=force,
                progress=step,
            )
    except Bm25Error as exc:
        typer.echo(f"BM25 indexing failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    t = Table(title="chatcde index bm25", show_header=False)
    t.add_column(style="bold")
    t.add_column(justify="right")
    t.add_row("Chunks in the catalogue", str(s.chunks))
    t.add_row("Indexed in this run", str(s.indexed))
    t.add_row("Up to date (skipped)", str(s.skipped))
    if s.removed:
        t.add_row("Removed (chunk gone)", str(s.removed))
    t.add_row("Terms / postings", f"{s.terms} / {s.postings}")
    t.add_row("Time", f"{s.seconds} s")
    Console().print(t)
    typer.echo(f"Catalogue: {cfg.data.catalogue}")


def _fmt(value: float | int | None, spec: str = ".3f") -> str:
    """Format an optional number for the results table ('-' when missing)."""
    if value is None:
        return "-"
    return format(value, spec) if isinstance(value, float) else str(value)


@index_app.command("search")
def index_search_cmd(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Search text, in any language.")],
    mode: Annotated[
        str,
        typer.Option(help="Retrievers to use: hybrid, bm25 or vector."),
    ] = "hybrid",
    rerank: Annotated[
        bool, typer.Option("--rerank/--no-rerank", help="Rerank the candidates.")
    ] = True,
    top_n: Annotated[
        int | None,
        typer.Option(min=1, help="Results to show (default retrieval.top_n)."),
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
    country: Annotated[
        list[str] | None, typer.Option(help="Country tag, ISO alpha-3 (repeatable).")
    ] = None,
) -> None:
    """Try the hybrid search and see every stage's ranks and scores (debugging).

    Prints the best chunks with their BM25, vector, fusion and rerank scores.
    The user-facing search with citations is `chatcde search` (issue #23).
    """
    import time

    from rich.console import Console
    from rich.table import Table

    from chatcde import catalogue
    from chatcde.schemas import Filters
    from chatcde.store.endpoint import EndpointClient, EndpointError
    from chatcde.store.filters import FilterError
    from chatcde.store.search import SearchError, SearchSettings, hybrid_search

    cfg = ctx.obj
    if mode not in ("hybrid", "bm25", "vector"):
        typer.echo("--mode must be hybrid, bm25 or vector.", err=True)
        raise typer.Exit(code=2)
    overrides: dict[str, object] = {"mode": mode, "rerank": rerank}
    if top_n is not None:
        overrides["top_n"] = top_n
    settings = SearchSettings.from_config(cfg, **overrides)
    filters = Filters(
        year_from=year_from,
        year_to=year_to,
        cde_units=unit or [],
        pub_types=pub_type or [],
        languages=lang or [],
        countries=country or [],
    )
    if not cfg.data.catalogue.is_file():
        typer.echo(f"No catalogue at {cfg.data.catalogue}.", err=True)
        raise typer.Exit(code=1)
    started = time.monotonic()
    con = catalogue.connect(cfg.data.catalogue, read_only=True)
    client = EndpointClient.from_config(cfg) if settings.needs_client else None
    try:
        hits = hybrid_search(con, client, query, settings, filters)
    except (SearchError, FilterError, EndpointError) as exc:
        typer.echo(f"Search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        con.close()
        if client is not None:
            client.close()
    seconds = time.monotonic() - started

    label = f"{mode}, reranked" if settings.will_rerank else mode
    t = Table(title=f"chatcde index search ({label})")
    for col in ("#", "Score", "Record", "Title", "Pages", "Source"):
        t.add_column(col)
    for col in ("BM25", "Vector", "RRF", "Rerank"):
        t.add_column(col, justify="right")
    for i, h in enumerate(hits, 1):
        c = h.chunk
        pages = (
            "-"
            if c.page_start is None
            else str(c.page_start)
            if c.page_end in (None, c.page_start)
            else f"{c.page_start}-{c.page_end}"
        )
        t.add_row(
            str(i),
            _fmt(h.score),
            c.record_id,
            h.title[:57] + ("..." if len(h.title) > 60 else ""),
            pages,
            c.source.value,
            f"{_fmt(h.bm25_rank)} ({_fmt(h.bm25_score, '.2f')})",
            f"{_fmt(h.vector_rank)} ({_fmt(h.vector_score)})",
            _fmt(h.rrf_score, ".4f"),
            _fmt(h.rerank_score),
        )
    Console().print(t)
    typer.echo(f"{len(hits)} results in {seconds:.2f} s")
