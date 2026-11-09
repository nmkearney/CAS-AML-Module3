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

from chatcde.cli_common import PANEL_QUERY
from chatcde.schemas import CountMatrix, Dimension, Level

if TYPE_CHECKING:
    from chatcde.config import Config

app = typer.Typer()

JsonOption = Annotated[
    bool, typer.Option("--json", help="Print the tool's result as JSON.")
]
LevelOption = Annotated[
    str,
    typer.Option(
        "--level",
        help="group: count method, data_type, setting, system and actors by group "
        "(additional_sub_area by impact area); term: term by term.",
    ),
]


def _level(value: str) -> Level:
    """Check a --level value; exit 2 if it is neither group nor term."""
    if value not in ("group", "term"):
        typer.echo(f"--level must be group or term, not {value!r}.", err=True)
        raise typer.Exit(code=2)
    return value  # type: ignore[return-value]


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


def _ids(record_ids: list[str], show: bool, limit: int = 3) -> str:
    """Record IDs for a table cell: all with ``--ids``, else the first few."""
    if show or len(record_ids) <= limit:
        return ", ".join(record_ids)
    return ", ".join(record_ids[:limit]) + f", ... (+{len(record_ids) - limit})"


@app.command(rich_help_panel=PANEL_QUERY)
def count(
    ctx: typer.Context,
    row: Annotated[Dimension, typer.Argument(help="Dimension for the rows.")],
    col: Annotated[
        Dimension | None, typer.Argument(help="Optional dimension for the columns.")
    ] = None,
    gaps: Annotated[
        bool,
        typer.Option(
            "--gaps", help="Show thin cells (find_gaps) instead of all counts."
        ),
    ] = False,
    max_ratio: Annotated[
        float, typer.Option(min=0.0, help="With --gaps: largest count / expected.")
    ] = 0.25,
    all_types: Annotated[
        bool,
        typer.Option(
            "--all-types",
            help="With --gaps: keep the types in gaps.exclude_types (conference "
            "items).",
        ),
    ] = False,
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
    show_ids: Annotated[
        bool, typer.Option("--ids", help="List every record ID, not only a few.")
    ] = False,
    level: LevelOption = "group",
    as_json: JsonOption = False,
) -> None:
    """Count publications by one or two schema dimensions, with denominators.

    Counts records (a record with two methods counts under both) and says how
    many matching records have no tag. With --gaps, lists the combinations of
    the two dimensions with far fewer records than expected if they were
    independent; conference items are left out unless --type or --all-types.
    """
    from rich.console import Console
    from rich.table import Table

    from chatcde.schemas import Filters
    from chatcde.tools import core, counts
    from chatcde.tools.context import get_context

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
    console = Console()
    if gaps:
        if col is None:
            typer.echo("--gaps needs two dimensions (ROW COL).", err=True)
            raise typer.Exit(code=2)
        report = _call(
            ctx.obj,
            lambda: counts.gap_report(
                get_context(),
                row,
                col,
                filters,
                max_ratio,
                level=_level(level),
                exclude_types=[] if all_types else None,
            ),
        )
        if as_json:
            typer.echo(_dump(report.gaps))
            return
        m, b = report.matrix, report.basis
        base = m.total_records - m.untagged_records
        typer.echo(
            f"{m.total_records} matching records; {base} tagged on both "
            f"{row.value} and {col.value} (confidence >= {filters.min_confidence})."
        )
        typer.echo(_levels_line(m))
        if b.excluded_types:
            typer.echo(
                f"Left out: {b.excluded_records} records of type "
                f"{', '.join(b.excluded_types)} (use --all-types to keep them)."
            )
        typer.echo(
            f"Gaps: count <= {max_ratio} x expected, among cells expected to hold "
            f"at least {report.min_expected:g} records."
        )
        if not report.gaps:
            typer.echo("No gaps found.")
            return
        t = Table(title=f"Gaps: {row.value} x {col.value}")
        t.add_column(row.value)
        t.add_column(col.value)
        for name in ("Count", "Expected", "Ratio"):
            t.add_column(name, justify="right")
        t.add_column("Records")
        for g in report.gaps:
            t.add_row(
                g.row,
                g.col or "",
                str(g.count),
                f"{g.expected:.1f}",
                f"{g.ratio:.2f}",
                _ids(g.record_ids, show_ids),
            )
        console.print(t)
        return

    m = _call(ctx.obj, lambda: core.count_by(row, col, filters, _level(level)))
    if as_json:
        typer.echo(_dump(m))
        return
    dims = row.value if col is None else f"{row.value} or {col.value}"
    typer.echo(
        f"{m.total_records} matching records; {m.untagged_records} have no tag on "
        f"{dims} (confidence >= {filters.min_confidence})."
    )
    typer.echo(_levels_line(m))
    base = m.total_records - m.untagged_records
    if col is None:
        t = Table(title=f"Records by {row.value}")
        t.add_column(row.value)
        t.add_column("Records", justify="right")
        t.add_column("Share", justify="right")
        t.add_column("Record IDs")
        for c in m.cells:
            share = (
                f"{100 * c.count / m.total_records:.0f}%" if m.total_records else "-"
            )
            t.add_row(c.row, str(c.count), share, _ids(c.record_ids, show_ids))
        console.print(t)
        typer.echo("Share = records with the term / matching records.")
        return
    typer.echo(f"{base} records are tagged on both dimensions.")
    found = {(c.row, c.col): c.count for c in m.cells}
    t = Table(title=f"Records by {row.value} x {col.value}")
    t.add_column(f"{row.value} \\ {col.value}")
    for c_term in m.col_totals:
        t.add_column(c_term, justify="right")
    t.add_column("Total", justify="right", style="bold")
    for r_term, r_total in m.row_totals.items():
        t.add_row(
            r_term,
            *[str(found.get((r_term, c), 0) or "") for c in m.col_totals],
            str(r_total),
        )
    t.add_row(
        "Total", *[str(n) for n in m.col_totals.values()], str(base), style="bold"
    )
    console.print(t)
    typer.echo("Empty cells are 0. Use --json for the record IDs behind each cell.")


def _levels_line(m: CountMatrix) -> str:
    """The line that says which level each axis was counted at."""
    parts = [f"{m.row_dimension.value} {m.row_level}"]
    if m.col_dimension is not None:
        parts.append(f"{m.col_dimension.value} {m.col_level}")
    return f"Counted by: {', '.join(parts)} (--level group|term)."


def _overview() -> None:
    """Print the 18 dimensions with vocabulary size and tag use."""
    from rich.console import Console
    from rich.table import Table

    from chatcde.tools import counts
    from chatcde.tools.context import get_context

    ctx = get_context()
    summaries, total = counts.dimension_overview(ctx)
    t = Table(title=f"Schema dimensions (vocabulary {ctx.cfg.vocab.version})")
    for name in ("#", "Dimension", "Terms", "Terms used", "Records tagged"):
        t.add_column(name, justify="left" if name == "Dimension" else "right")
    for i, s in enumerate(summaries, 1):
        t.add_row(
            str(i),
            s.dimension.value,
            str(s.terms),
            str(s.terms_used),
            f"{s.records_tagged} of {total}",
        )
    Console().print(t)
    typer.echo(
        "Counts use tags with confidence >= 0.5. Terms: `chatcde vocab -d "
        "<dimension>`. Scope notes: docs/schema_draft.md."
    )


@app.command(rich_help_panel=PANEL_QUERY)
def schema(ctx: typer.Context) -> None:
    """Show the 18 schema dimensions with vocabulary size and how much is tagged.

    Scope notes for each dimension are in docs/schema_draft.md.
    """
    _call(ctx.obj, _overview)


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


def vocab_list(
    ctx: typer.Context,
    dimension: Annotated[
        Dimension | None,
        typer.Option("--dimension", "-d", help="List this dimension's terms."),
    ] = None,
    level: Annotated[
        str,
        typer.Option(
            "--level", help="term: terms with their group; group: the groups only."
        ),
    ] = "term",
    as_json: JsonOption = False,
) -> None:
    """List vocabulary terms with record counts; ``vocab extract`` drafts new ones.

    Without --dimension, shows every dimension with its number of terms. With
    it, shows each term's code, label, group, records tagged and definition (the
    same list the assistant gets from list_vocabulary); --level group lists the
    groups with the records tagged with any of their terms.
    """
    if ctx.invoked_subcommand is not None:
        return
    if dimension is None:
        if as_json:
            typer.echo("--json needs --dimension.", err=True)
            raise typer.Exit(code=2)
        _call(ctx.obj, _overview)
        return

    from rich.console import Console
    from rich.table import Table

    from chatcde.tools import core

    lvl = _level(level)
    terms = _call(ctx.obj, lambda: core.list_vocabulary(dimension, lvl))
    if as_json:
        typer.echo(_dump(terms))
        return
    grouped = any(t.group for t in terms)
    title = "groups" if lvl == "group" and not grouped else "terms"
    t = Table(title=f"Vocabulary: {dimension.value} ({title})")
    names = ["Code", "Label", *(["Group"] if grouped else []), "Records", "Definition"]
    for name in names:
        t.add_column(name, justify="right" if name == "Records" else "left")
    for term in terms:
        t.add_row(
            term.code,
            term.label if term.label != term.code else "",
            *([term.group or ""] if grouped else []),
            str(term.record_count),
            textwrap.shorten(term.definition or "", width=70, placeholder=" ..."),
        )
    Console().print(t)
    typer.echo(
        "Records = records with a tag of the term (or of any term of the group) at "
        "confidence >= 0.5."
    )
