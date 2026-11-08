"""``chatcde tag``, plus ``vocab extract`` and ``evaluate tagging``.

``vocab_extract`` and ``evaluate_tagging`` are not registered on :data:`app`;
``chatcde.cli`` places them in the ``vocab`` and ``evaluate`` groups.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from chatcde.cli_common import PANEL_PIPELINE, not_implemented

app = typer.Typer()


@app.command(rich_help_panel=PANEL_PIPELINE)
def tag(
    force: Annotated[
        bool, typer.Option(help="Redo work that is already done.")
    ] = False,
) -> None:
    """Tag records against the vocabulary with the local LLM."""
    not_implemented("tag", 15)


class ExtractStep(StrEnum):
    """Steps of ``chatcde vocab extract``."""

    SAMPLE = "sample"
    LABEL = "label"
    PROPOSE = "propose"
    ALL = "all"


def vocab_extract(
    ctx: typer.Context,
    step: Annotated[
        ExtractStep, typer.Option(help="Run one step, or all three in order.")
    ] = ExtractStep.ALL,
    force: Annotated[
        bool, typer.Option(help="Redo the chosen step(s) even if already done.")
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option(min=1, help="Label at most this many records (for trying out)."),
    ] = None,
) -> None:
    """Open extraction on a stratified sample, for drafting the vocabulary.

    sample: draw ~250 records stratified by CDE unit, decade and language.

    label: the tagging model writes free-text labels (methods, themes, data types,
    settings, systems, actors) with evidence quotes.

    propose: cluster the labels by embedding and let the model name and define
    each cluster; writes vocab/candidates.yaml for curation.

    Each step skips finished work unless --force. See decision 0019.
    """
    from rich.console import Console

    from chatcde import catalogue
    from chatcde.store.embeddings import EmbeddingError
    from chatcde.store.endpoint import EndpointClient, EndpointError

    cfg = ctx.obj
    steps = (
        [ExtractStep.SAMPLE, ExtractStep.LABEL, ExtractStep.PROPOSE]
        if step is ExtractStep.ALL
        else [step]
    )
    console = Console()
    con = catalogue.connect(cfg.data.catalogue)
    try:
        if ExtractStep.SAMPLE in steps:
            from chatcde.tag.sample import build_sample

            console.print(_sample_table(build_sample(con, cfg, force=force)))
        if ExtractStep.LABEL in steps or ExtractStep.PROPOSE in steps:
            with EndpointClient.from_config(cfg) as client:
                if ExtractStep.LABEL in steps:
                    console.print(
                        _label_table(_run_label(con, cfg, client, force, limit))
                    )
                if ExtractStep.PROPOSE in steps:
                    console.print(_propose_table(_run_propose(con, cfg, client, force)))
    except (RuntimeError, EndpointError, EmbeddingError, FileNotFoundError) as exc:
        typer.echo(f"vocab extract failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        con.close()
    typer.echo(f"Catalogue: {cfg.data.catalogue}")


def _progress() -> Any:
    """A transient progress bar on stderr."""
    from rich.console import Console
    from rich.progress import Progress

    return Progress(console=Console(stderr=True), transient=True)


def _run_label(con: Any, cfg: Any, client: Any, force: bool, limit: int | None) -> Any:
    """Run the label step with a progress bar."""
    from chatcde.tag.extract import label_sample

    with _progress() as bar:
        task = bar.add_task(f"Labelling with {cfg.models.tagging}", total=None)
        return label_sample(
            con,
            cfg,
            client,
            force=force,
            limit=limit,
            progress=lambda d, t: bar.update(task, completed=d, total=t),
        )


def _run_propose(con: Any, cfg: Any, client: Any, force: bool) -> Any:
    """Run the propose step with a progress bar."""
    from chatcde.tag.propose import propose_terms

    with _progress() as bar:
        task = bar.add_task("Proposing terms", total=None)
        return propose_terms(
            con,
            cfg,
            client,
            force=force,
            progress=lambda d, t: bar.update(task, completed=d, total=t),
        )


def _table(title: str) -> Any:
    """A two-column summary table."""
    from rich.table import Table

    t = Table(title=title, show_header=False)
    t.add_column(style="bold")
    t.add_column(justify="right")
    return t


def _sample_table(s: Any) -> Any:
    """Summary of the sample step."""
    t = _table("vocab extract: sample")
    t.add_row("Publications in the catalogue", str(s.publications))
    t.add_row("Eligible (abstract or page text)", str(s.eligible))
    t.add_row("Non-empty strata (unit x decade x language)", str(s.strata))
    t.add_row(
        "Sampled" + (" (kept from earlier run)" if s.reused else ""), str(s.sampled)
    )
    t.add_row("  with page text", str(s.with_pages))
    for name, counts in (
        ("unit", s.by_unit),
        ("decade", s.by_decade),
        ("language", s.by_language),
    ):
        t.add_row(f"By {name}", ", ".join(f"{k} {v}" for k, v in counts.items()))
    return t


def _label_table(s: Any) -> Any:
    """Summary of the label step."""
    t = _table("vocab extract: label")
    t.add_row("Model / prompt", f"{s.model} / {s.prompt_version}")
    t.add_row("Sampled records", str(s.sampled))
    t.add_row("Labelled in this run", str(s.labelled))
    t.add_row("Failed in this run", str(s.failed))
    t.add_row("Already done (skipped)", str(s.skipped))
    if s.remaining:
        t.add_row("Left for a later run", str(s.remaining))
    if s.stopped:
        t.add_row("Stopped", "endpoint kept failing")
    t.add_row("Records labelled (total)", f"{s.ok_total} of {s.sampled}")
    for kind, n in s.labels_by_kind.items():
        t.add_row(f"  {kind} labels", str(n))
    if s.evidence_found_share is not None:
        t.add_row("Quotes found in the text", f"{s.evidence_found_share:.0%}")
    return t


def _propose_table(s: Any) -> Any:
    """Summary of the propose step."""
    t = _table("vocab extract: propose")
    t.add_row("Records labelled (denominator)", str(s.records))
    for kind in s.labels:
        t.add_row(
            kind,
            f"{s.labels[kind]} labels, {s.distinct[kind]} distinct, "
            f"{s.clusters[kind]} clusters, {s.candidates[kind]} candidates",
        )
    t.add_row(
        "Proposals: new / cached / failed", f"{s.proposed} / {s.cached} / {s.failed}"
    )
    t.add_row("Written to", s.output)
    return t


def evaluate_tagging() -> None:
    """Score LLM tags against the hand-tagged reference set."""
    not_implemented("evaluate tagging", 16)


def vocab_review(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option(help="Overwrite an existing workbook (loses its decisions).")
    ] = False,
    carry_from: Annotated[
        Path | None,
        typer.Option(
            dir_okay=False, exists=True, help="Copy decisions from an older workbook."
        ),
    ] = None,
) -> None:
    """Write the curation workbook (Excel) from the vocabulary and the candidates.

    One sheet per curated dimension: current terms (decision 'keep') and the
    candidates from `vocab extract` (decide each). Edit the white columns in
    Excel, then run `chatcde vocab apply`. See decision 0026.
    """
    from chatcde.tag.curation import export_workbook

    cfg = ctx.obj
    out = cfg.vocab.workbook()
    try:
        counts = export_workbook(
            cfg.vocab.file,
            cfg.vocab_extract.output,
            out,
            force=force,
            carry_from=carry_from,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        typer.echo(f"vocab review failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    t = _table("vocab review")
    for sheet, n in counts.items():
        t.add_row(sheet, f"{n} rows")
    t.add_row("Written to", str(out))
    from rich.console import Console

    Console().print(t)


def vocab_apply(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option(help="Check the workbook and summarise; write nothing.")
    ] = False,
) -> None:
    """Check the curation workbook and write draft_vocabulary.yaml from it.

    Lists every problem and writes nothing if any row is invalid. Rewrites only
    the curated dimensions (and sub-area scope notes); other blocks are kept as
    they are. Running it twice on the same workbook gives the same file.
    """
    from rich.console import Console

    from chatcde.tag.curation import CurationError, apply_workbook

    cfg = ctx.obj
    workbook = cfg.vocab.workbook()
    if not workbook.is_file():
        typer.echo(f"No workbook at {workbook}; run `chatcde vocab review`.", err=True)
        raise typer.Exit(code=1)
    try:
        s = apply_workbook(
            workbook,
            cfg.vocab.file,
            version=cfg.vocab.version,
            terms_range=cfg.vocab.terms_per_dimension,
            code_map_path=cfg.vocab.code_map(),
            dry_run=dry_run,
        )
    except ValueError as exc:  # workbook without a base sheet
        typer.echo(f"vocab apply failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except CurationError as exc:
        typer.echo(f"The workbook has {len(exc.problems)} problem(s):", err=True)
        for p in exc.problems:
            typer.echo(f"  - {p}", err=True)
        typer.echo("Nothing was written.", err=True)
        raise typer.Exit(code=1) from exc
    t = _table("vocab apply" + (" (dry run)" if dry_run else ""))
    for dim, n in s.terms.items():
        parts = [
            f"{name} {getattr(s, name).get(dim)}"
            for name in ("added", "renamed", "merged", "retired", "deferred")
            if getattr(s, name).get(dim)
        ]
        t.add_row(dim, f"{n} terms" + (f" ({', '.join(parts)})" if parts else ""))
    t.add_row("Sub-area scope notes changed", str(s.scope_notes_changed))
    t.add_row("Written to", "nothing (dry run)" if dry_run else s.output)
    Console().print(t)
    if s.warnings:
        typer.echo(f"{len(s.warnings)} warning(s):", err=True)
        for w in s.warnings:
            typer.echo(f"  - {w}", err=True)
