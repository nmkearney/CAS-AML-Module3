"""Direct tool calls without the LLM: ``search``, ``show``, ``read``, ``count``, ...

Thin wrappers around :mod:`chatcde.tools`: each command opens a
:class:`~chatcde.tools.context.ToolContext` from the CLI's config, calls the
same function the agent calls, and prints the result (or ``--json``).
``vocab_list`` is not registered on :data:`app`; ``chatcde.cli`` uses it as the
``vocab`` group's default action.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

import typer

from chatcde.cli_common import PANEL_QUERY, not_implemented

if TYPE_CHECKING:
    from chatcde.config import Config

app = typer.Typer()

JsonOption = Annotated[
    bool, typer.Option("--json", help="Print the tool's result as JSON.")
]


def _call[T](cfg: Config, fn: Callable[[], T]) -> T:
    """Run a tool call inside a context built from ``cfg``; exit 1 on ToolError."""
    from chatcde.tools.context import ToolContext, ToolError, use_context

    try:
        with use_context(ToolContext(cfg)):
            return fn()
    except ToolError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _dump(result: object) -> str:
    """A pydantic result, or a list of them, as indented JSON."""
    from pydantic import BaseModel

    if isinstance(result, list):
        data: object = [r.model_dump(mode="json") for r in result]
    elif isinstance(result, BaseModel):
        data = result.model_dump(mode="json")
    else:  # pragma: no cover
        data = result
    return json.dumps(data, indent=2, ensure_ascii=False)


def _pages(start: int | None, end: int | None) -> str:
    """'p. 4', 'pp. 4-5', or '' for chunks without pages."""
    if start is None:
        return ""
    return f"p. {start}" if end in (None, start) else f"pp. {start}-{end}"


@app.command(rich_help_panel=PANEL_QUERY)
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Search text, in any language.")],
    k: Annotated[int, typer.Option("-k", min=1, help="Passages to return.")] = 8,
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
    region: Annotated[
        list[str] | None, typer.Option(help="Region tag (repeatable).")
    ] = None,
    sub_area: Annotated[
        list[str] | None, typer.Option(help="Sub-area tag, e.g. SL-d (repeatable).")
    ] = None,
    method: Annotated[
        list[str] | None, typer.Option(help="Method tag (repeatable).")
    ] = None,
    sdg: Annotated[
        list[int] | None, typer.Option(help="SDG number (repeatable).")
    ] = None,
    min_confidence: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Lowest tag confidence counted.")
    ] = 0.5,
    full: Annotated[
        bool, typer.Option("--full", help="Print whole passages, not snippets.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Search publications and show matching passages with BORIS IDs.

    The same hybrid search the assistant uses (search_publications): keywords
    and embeddings, reranked. Needs the endpoint for the query embedding and
    the reranker (key in the variable named by llm.api_key_env).
    """
    from chatcde.schemas import Filters
    from chatcde.tools import core

    filters = Filters(
        year_from=year_from,
        year_to=year_to,
        cde_units=unit or [],
        pub_types=pub_type or [],
        languages=lang or [],
        countries=country or [],
        regions=region or [],
        sub_areas=sub_area or [],
        methods=method or [],
        sdgs=sdg or [],
        min_confidence=min_confidence,
    )
    passages = _call(ctx.obj, lambda: core.search_publications(query, filters, k))
    if as_json:
        typer.echo(_dump(passages))
        return
    if not passages:
        typer.echo("No passages found.")
        return
    for i, p in enumerate(passages, 1):
        cit, chunk = p.citation, p.chunk
        head = f"{i}. [{p.score:.3f}] {cit.title}"
        if cit.year:
            head += f" ({cit.year})"
        typer.echo(head)
        where = [
            _pages(chunk.page_start, chunk.page_end),
            chunk.source.value,
            cit.record_id,
            f"doi:{cit.doi}" if cit.doi else "",
        ]
        typer.echo("   " + " | ".join(x for x in where if x))
        text = " ".join(chunk.text.split())
        if not full:
            text = textwrap.shorten(text, width=300, placeholder=" ...")
        typer.echo(textwrap.indent(textwrap.fill(text, width=85), "   "))
        typer.echo("")


@app.command(rich_help_panel=PANEL_QUERY)
def show(
    ctx: typer.Context,
    record: Annotated[
        str, typer.Argument(help="BORIS ID, DOI or handle of the publication.")
    ],
    as_json: JsonOption = False,
) -> None:
    """Show one publication: metadata, authors, abstract, tags, full-text status.

    The same data the assistant gets from get_publication. No endpoint needed.
    """
    from chatcde.tools import core

    d = _call(ctx.obj, lambda: core.get_publication(record))
    if as_json:
        typer.echo(_dump(d))
        return
    r = d.record
    typer.echo(r.title + (f" ({r.year})" if r.year else ""))
    if d.authors:
        names = [a.name + ("*" if a.is_cde else "") for a in d.authors]
        typer.echo("Authors:   " + "; ".join(names) + "   (* = CDE)")
    rows = [
        ("Type", r.pub_type),
        ("In", r.container),
        ("Languages", ", ".join(r.languages)),
        ("CDE units", ", ".join(r.cde_units)),
        ("Keywords", ", ".join(r.keywords)),
        ("DOI", r.publisher_doi),
        ("BORIS DOI", r.doi),
        ("BORIS", f"{r.boris_url}  ({r.record_id})"),
        ("Access", r.access.value),
        ("Full text", "yes" if r.has_fulltext else "no"),
        ("Download", d.download.status.value if d.download else None),
    ]
    for label, value in rows:
        if value:
            typer.echo(f"{label + ':':<11}{value}")
    if r.abstract:
        source = r.abstract_source.value if r.abstract_source else "unknown"
        typer.echo(f"\nAbstract ({source}):")
        typer.echo(textwrap.fill(" ".join(r.abstract.split()), width=88))
    if d.tags:
        typer.echo("\nTags (confidence):")
        by_dim: dict[str, list[str]] = {}
        for t in d.tags:
            label = t.other_label if t.term == "other" and t.other_label else t.term
            role = f", {t.role.value}" if t.role else ""
            by_dim.setdefault(t.dimension.value, []).append(
                f"{label} ({t.confidence:.2f}{role})"
            )
        for dim, terms in by_dim.items():
            typer.echo(f"  {dim + ':':<27}{'; '.join(terms)}")


@app.command(rich_help_panel=PANEL_QUERY)
def read(
    ctx: typer.Context,
    record: Annotated[str, typer.Argument(help="BORIS ID, DOI or handle.")],
    page_start: Annotated[int, typer.Argument(min=1, help="First page (1-based).")],
    page_end: Annotated[
        int | None, typer.Argument(min=1, help="Last page (default: first page).")
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Print the full text of a page range of one publication, for quoting.

    The same passages the assistant gets from read_section. No endpoint needed.
    """
    from chatcde.tools import core

    chunks = _call(ctx.obj, lambda: core.read_section(record, page_start, page_end))
    if as_json:
        typer.echo(_dump(chunks))
        return
    for c in chunks:
        typer.echo(f"--- {_pages(c.page_start, c.page_end)} ({c.chunk_id}) ---")
        typer.echo(textwrap.fill(c.text, width=88))
        typer.echo("")


@app.command(rich_help_panel=PANEL_QUERY)
def count() -> None:
    """Count publications by one or two schema dimensions."""
    not_implemented("count", 24)


@app.command(rich_help_panel=PANEL_QUERY)
def schema() -> None:
    """Show the schema dimensions and their scope notes."""
    not_implemented("schema", 24)


@app.command(rich_help_panel=PANEL_QUERY)
def coverage() -> None:
    """Report how many records have metadata, abstracts, full text and tags."""
    not_implemented("coverage", 11)


def vocab_list(ctx: typer.Context) -> None:
    """List vocabulary terms; ``vocab extract`` drafts new ones."""
    if ctx.invoked_subcommand is None:
        not_implemented("vocab", 24)
