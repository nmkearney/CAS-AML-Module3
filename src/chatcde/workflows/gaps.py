"""The ``chatcde gaps`` workflow: a fixed tool chain, then one judgement by the LLM.

Issue #27; see docs/decisions/0024-gaps-workflow.md. A free agent might count,
search and judge in any order. Here the chain is fixed in code, so every run
follows the same steps and the same output format, even with a mid-sized
local model:

1. **Count** (code): :func:`chatcde.tools.counts.gap_report` for the two
   dimensions within the scope. Conference items are left out unless the
   filters name types (decision 0018). Its cells, expected counts and
   denominators are the only numbers in the output.
2. **Look nearby** (code): for each of the first ``gaps.max_cells`` gap
   cells, :func:`chatcde.tools.publications.search_publications` searches
   the scope for the two terms' labels. Each record found is shown with its
   own tags on both dimensions. A record that is about the combination but
   tagged otherwise points to a tagging artefact; none at all points to a
   real gap.
3. **Caveats** (code): untagged records, left-out types, tag confidence,
   cells not shown, and the data edition and full-text coverage
   (:func:`chatcde.workflows.answer.data_note`).
4. **Judge** (LLM, ``models.agent``): one request with the versioned prompt
   ``gaps.prompt`` and the evidence as JSON. The reply must follow
   :class:`GapsJudgement` (JSON schema). Each cell is sorted as a real gap,
   a tagging artefact, a deliberate choice (outside CDE's mandate or scope),
   or unclear, with a short reason and the evidence record IDs. IDs that are
   not in the cell's evidence are dropped and reported. A failed or invalid
   reply is reported too; the counts and evidence are still shown.

The :class:`GapsResult` records every input and output with the model, prompt,
vocabulary and code versions (CLAUDE.md: reproducibility).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from chatcde.agent import AgentError, load_prompt
from chatcde.config import Config
from chatcde.harvest.common import code_version, now
from chatcde.schemas import Citation, Dimension, Filters, GapCell, Passage
from chatcde.store.endpoint import EndpointError
from chatcde.tools.context import ToolContext, ToolError
from chatcde.tools.counts import gap_report
from chatcde.tools.publications import citation, records_by_id, search_publications
from chatcde.tools.vocabulary import (
    VocabularyError,
    dimension_entries,
    load_vocabulary,
)
from chatcde.workflows.answer import data_note

log = logging.getLogger(__name__)

Verdict = Literal["real_gap", "tagging_artefact", "deliberate_choice", "unclear"]

#: How each verdict is shown in the terminal.
VERDICT_LABELS: dict[str, str] = {
    "real_gap": "real gap",
    "tagging_artefact": "tagging artefact",
    "deliberate_choice": "deliberate choice",
    "unclear": "unclear",
}

#: Characters of each nearby passage passed to the model and shown with --json.
SNIPPET_CHARS = 400

#: Records of a non-empty cell shown with their titles.
CELL_RECORDS = 5

#: Below this share of matching records tagged on both dimensions, add a caveat.
LOW_TAGGING_SHARE = 0.5


class GapsError(Exception):
    """The workflow cannot run (bad dimensions, no catalogue or tags)."""


# --- evidence (code) ------------------------------------------------------------------


class NearbyRecord(BaseModel):
    """A record found by searching the scope for a gap cell's two terms."""

    citation: Citation
    snippet: str = Field(description="Start of the best-matching passage")
    score: float
    row_terms: list[str] = Field(description="The record's tags on the row dimension")
    col_terms: list[str] = Field(
        description="The record's tags on the column dimension"
    )


class CellEvidence(BaseModel):
    """One gap cell with its counts (from code) and what a search found nearby."""

    row: str
    col: str
    row_label: str
    col_label: str
    count: int
    expected: float
    ratio: float
    record_ids: list[str] = Field(description="Records tagged with both terms")
    records: list[Citation] = Field(
        description="The first few of record_ids, with title and year"
    )
    nearby: list[NearbyRecord]
    search_error: str | None = None

    def evidence_ids(self) -> set[str]:
        """Record IDs the model may cite for this cell."""
        return set(self.record_ids) | {n.citation.record_id for n in self.nearby}


# --- judgement (LLM) ------------------------------------------------------------------


class CellJudgement(BaseModel):
    """The model's reading of one gap cell."""

    row: str = Field(description="Row term code, as given")
    col: str = Field(description="Column term code, as given")
    verdict: Verdict
    reason: str = Field(description="One to three sentences, no numbers of your own")
    evidence: list[str] = Field(
        description="record_id values from this cell's evidence that support it"
    )


class GapsJudgement(BaseModel):
    """What the model must return (sent as the JSON schema of the reply)."""

    summary: str = Field(description="A short paragraph on the gaps as a whole")
    cells: list[CellJudgement]
    caveats: list[str] = Field(
        description="Limits of the evidence the reader should know"
    )


def judgement_schema() -> dict[str, Any]:
    """:class:`GapsJudgement` as a strict JSON schema (no titles, no ``$ref``)."""
    schema = GapsJudgement.model_json_schema()
    defs = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return clean(defs[node["$ref"].rsplit("/", 1)[-1]])
            out = {k: clean(v) for k, v in node.items() if k != "title"}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}))
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(schema)


# --- result ---------------------------------------------------------------------------


class GapsResult(BaseModel):
    """A gap analysis: counts and evidence from code, the model's judgement."""

    row: Dimension
    col: Dimension
    filters: Filters = Field(description="The filters as used, with types excluded")
    total_records: int = Field(description="Records matching the scope")
    tagged_on_both: int = Field(description="Base of the matrix")
    excluded_types: list[str]
    excluded_records: int
    max_ratio: float
    min_expected: float
    gaps_found: int = Field(description="All gap cells, before max_cells")
    cells: list[CellEvidence]
    caveats: list[str] = Field(description="Caveats computed by code")
    data_note: str | None
    judgement: GapsJudgement | None
    judgement_error: str | None = None
    dropped_ids: list[str] = Field(
        default_factory=list,
        description="IDs the model cited that were not in the cell's evidence",
    )
    usage: dict[str, int] = Field(default_factory=dict)
    seconds: float
    model: str | None
    prompt_version: str | None
    vocab_version: str
    code_version: str
    created_at: datetime


def parse_dims(text: str) -> tuple[Dimension, Dimension]:
    """``"sub_area,method"`` -> the two dimensions.

    Raises:
        GapsError: Not two different, known dimensions.
    """
    parts = [p.strip() for p in text.split(",") if p.strip()]
    known = ", ".join(d.value for d in Dimension)
    if len(parts) != 2:
        raise GapsError(f"--dims needs two dimensions, e.g. sub_area,method ({known})")
    try:
        row, col = Dimension(parts[0]), Dimension(parts[1])
    except ValueError as exc:
        raise GapsError(f"unknown dimension in {text!r}; use two of: {known}") from exc
    if row == col:
        raise GapsError("--dims needs two different dimensions")
    return row, col


def _labels(cfg: Config, dimension: Dimension) -> dict[str, str]:
    """Code -> label for one dimension (empty if the vocabulary can't be read)."""
    try:
        vocab = load_vocabulary(cfg.vocab.file)
    except VocabularyError as exc:
        log.warning("vocabulary not readable, using codes as labels: %s", exc)
        return {}
    return {e.code: e.label for e in dimension_entries(vocab, dimension)}


def _tags_of(
    ctx: ToolContext,
    record_ids: list[str],
    dimension: Dimension,
    min_confidence: float,
) -> dict[str, list[str]]:
    """Each record's terms on ``dimension`` (confidence at least ``min_confidence``)."""
    if not record_ids:
        return {}
    with ctx.catalogue() as con:
        rows = con.execute(
            "SELECT record_id, term FROM tags WHERE dimension = ? AND confidence >= ? "
            "AND record_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY record_id, term",
            [dimension.value, min_confidence, record_ids],
        ).fetchall()
    out: dict[str, list[str]] = {}
    for rid, term in rows:
        out.setdefault(rid, []).append(str(term))
    return out


Search = Callable[[ToolContext, str, Filters, int], list[Passage]]


def _default_search(
    ctx: ToolContext, query: str, filters: Filters, k: int
) -> list[Passage]:
    """The real search; looked up at call time so tests can replace it."""
    return search_publications(ctx, query, filters, k)


def collect_evidence(
    ctx: ToolContext,
    row: Dimension,
    col: Dimension,
    cells: list[GapCell],
    filters: Filters,
    *,
    passages: int,
    search: Search | None = None,
) -> list[CellEvidence]:
    """Step 2 of the chain: search each gap cell's two terms within the scope.

    The search may return several passages of one record; each record is kept
    once, with its best passage. A failing search (no index, endpoint down) is
    recorded on the cell, not raised, so the counts are still shown.
    """
    search = search or _default_search
    row_labels, col_labels = _labels(ctx.cfg, row), _labels(ctx.cfg, col)
    out = []
    for g in cells:
        c = g.col or ""
        r_label, c_label = row_labels.get(g.row, g.row), col_labels.get(c, c)
        error = None
        hits: list[Passage] = []
        try:
            hits = search(ctx, f"{r_label} {c_label}", filters, passages * 2)
        except ToolError as exc:
            error = str(exc)
            log.warning("gaps: search for %s x %s failed: %s", g.row, c, exc)
        best: dict[str, Passage] = {}
        for p in hits:
            best.setdefault(p.citation.record_id, p)
        chosen = list(best.values())[:passages]
        ids = [p.citation.record_id for p in chosen]
        r_tags = _tags_of(ctx, ids, row, filters.min_confidence)
        c_tags = _tags_of(ctx, ids, col, filters.min_confidence)
        nearby = [
            NearbyRecord(
                citation=p.citation,
                snippet=" ".join(p.chunk.text.split())[:SNIPPET_CHARS],
                score=round(p.score, 4),
                row_terms=r_tags.get(p.citation.record_id, []),
                col_terms=c_tags.get(p.citation.record_id, []),
            )
            for p in chosen
        ]
        with ctx.catalogue() as con:
            found = records_by_id(con, g.record_ids[:CELL_RECORDS])
        out.append(
            CellEvidence(
                row=g.row,
                col=c,
                row_label=r_label,
                col_label=c_label,
                count=g.count,
                expected=g.expected,
                ratio=g.ratio,
                record_ids=g.record_ids,
                records=[citation(found[r]) for r in g.record_ids if r in found],
                nearby=nearby,
                search_error=error,
            )
        )
    return out


def code_caveats(result: GapsResult, min_confidence: float) -> list[str]:
    """Step 3: the limits of the counts, stated by code with their numbers."""
    out = []
    untagged = result.total_records - result.tagged_on_both
    if result.total_records:
        out.append(
            f"{untagged:,} of {result.total_records:,} matching records have no tag "
            f"on {result.row.value} or {result.col.value} (confidence >= "
            f"{min_confidence}) and are not counted."
        )
        if result.tagged_on_both < LOW_TAGGING_SHARE * result.total_records:
            out.append(
                "Fewer than half of the matching records are tagged on both "
                "dimensions, so a thin cell may reflect missing tags."
            )
    if result.excluded_types:
        out.append(
            f"{result.excluded_records:,} records of type "
            f"{', '.join(result.excluded_types)} are left out (decision 0018; "
            "use --all-types to keep them)."
        )
    out.append(
        f"Only combinations expected to hold at least {result.min_expected:g} "
        "records are judged; rarer pairs say nothing either way."
    )
    if result.gaps_found > len(result.cells):
        out.append(
            f"{len(result.cells)} of {result.gaps_found} gaps are shown (the most "
            "extreme first; raise --max-cells for more)."
        )
    if any(c.search_error for c in result.cells):
        out.append(
            "The search for nearby records failed for some cells, so the "
            "judgement there rests on counts only."
        )
    return out


# --- step 4: the judgement ------------------------------------------------------------


def _evidence_payload(result: GapsResult) -> dict[str, Any]:
    """The JSON the model reads: scope, denominators, cells, caveats."""
    return {
        "dimensions": {"row": result.row.value, "col": result.col.value},
        "scope": result.filters.model_dump(exclude_defaults=True),
        "matching_records": result.total_records,
        "tagged_on_both": result.tagged_on_both,
        "gap_rule": (
            f"count <= {result.max_ratio} x expected, where expected = row total x "
            f"column total / {result.tagged_on_both}"
        ),
        "cells": [
            {
                "row": c.row,
                "row_label": c.row_label,
                "col": c.col,
                "col_label": c.col_label,
                "count": c.count,
                "expected": c.expected,
                "records": [
                    {"record_id": r.record_id, "title": r.title, "year": r.year}
                    for r in c.records
                ],
                "nearby": [
                    {
                        "record_id": n.citation.record_id,
                        "title": n.citation.title,
                        "year": n.citation.year,
                        f"tags_{result.row.value}": n.row_terms,
                        f"tags_{result.col.value}": n.col_terms,
                        "passage": n.snippet,
                    }
                    for n in c.nearby
                ],
                **({"search_error": c.search_error} if c.search_error else {}),
            }
            for c in result.cells
        ],
        "caveats": result.caveats,
        "data": result.data_note,
    }


def _parse_judgement(content: str) -> GapsJudgement:
    """Read the model's JSON reply (tolerating a Markdown code fence)."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return GapsJudgement.model_validate_json(text)


def _check_judgement(
    judgement: GapsJudgement, cells: list[CellEvidence]
) -> tuple[GapsJudgement, list[str]]:
    """Keep one judgement per given cell, in table order; drop unsupported IDs.

    Cells the model left out become ``unclear``; cells it invented are ignored.
    """
    by_key = {(j.row, j.col): j for j in judgement.cells}
    kept, dropped = [], []
    for c in cells:
        j = by_key.get((c.row, c.col))
        if j is None:
            kept.append(
                CellJudgement(
                    row=c.row,
                    col=c.col,
                    verdict="unclear",
                    reason="The model gave no judgement for this cell.",
                    evidence=[],
                )
            )
            continue
        allowed = c.evidence_ids()
        dropped += [rid for rid in j.evidence if rid not in allowed]
        kept.append(
            j.model_copy(
                update={"evidence": [rid for rid in j.evidence if rid in allowed]}
            )
        )
    extra = len(judgement.cells) - sum(1 for c in cells if (c.row, c.col) in by_key)
    if extra > 0:
        log.warning("gaps: ignored %d judgements for cells not given", extra)
    return judgement.model_copy(update={"cells": kept}), sorted(set(dropped))


def judge(
    ctx: ToolContext, result: GapsResult, model: str
) -> tuple[GapsJudgement, list[str], dict[str, int]]:
    """Step 4: one structured request to the agent model.

    Returns:
        The checked judgement, the dropped record IDs and the token usage.

    Raises:
        AgentError: The reply is missing, not JSON or not a valid judgement.
        chatcde.store.endpoint.EndpointError: The request fails.
    """
    cfg = ctx.cfg
    s = cfg.gaps
    schema = judgement_schema()
    system = load_prompt(cfg.prompts.dir, s.prompt).replace(
        "{schema}", json.dumps(schema, ensure_ascii=False)
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    _evidence_payload(result), ensure_ascii=False, indent=1
                ),
            },
        ],
        "temperature": s.temperature,
        "max_tokens": s.max_tokens,
        **cfg.agent.extra_body,
    }
    if s.structured_output:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "gaps_judgement", "schema": schema, "strict": True},
        }
    body = ctx.endpoint().post_json("/chat/completions", payload)
    choices = body.get("choices") or []
    message = choices[0].get("message") if choices else None
    if not isinstance(message, dict) or not message.get("content"):
        raise AgentError(f"no answer in the reply: {str(body)[:200]}")
    if choices[0].get("finish_reason") == "length":
        raise AgentError("the reply was cut off; raise gaps.max_tokens")
    try:
        parsed = _parse_judgement(message["content"])
    except ValidationError as exc:
        raise AgentError(f"the reply is not a valid judgement: {exc}") from exc
    usage = {k: v for k, v in (body.get("usage") or {}).items() if isinstance(v, int)}
    checked, dropped = _check_judgement(parsed, result.cells)
    return checked, dropped, usage


def run_gaps(
    ctx: ToolContext,
    row: Dimension,
    col: Dimension,
    filters: Filters | None = None,
    *,
    max_ratio: float | None = None,
    max_cells: int | None = None,
    all_types: bool = False,
    use_llm: bool = True,
    model: str | None = None,
    search: Search | None = None,
) -> GapsResult:
    """Run the whole gap workflow (steps 1-4 above).

    Args:
        ctx: Tool context; its endpoint client serves search and judgement.
        row: Row dimension, e.g. ``sub_area``.
        col: Column dimension, e.g. ``method``.
        filters: The scope (unit, sub-area, region, years, ...).
        max_ratio: Largest ``count / expected`` that counts as a gap; default
            ``gaps.max_ratio``.
        max_cells: Gap cells to look into and judge; default ``gaps.max_cells``.
        all_types: Keep conference items (and every other type).
        use_llm: ``False`` stops after step 3 (no model call).
        model: Override for ``models.agent``.
        search: Search function; default :func:`search_publications` (tests
            replace it).

    Raises:
        GapsError: Same dimension twice, no records or no tags (from step 1).
    """
    started = time.monotonic()
    cfg = ctx.cfg
    s = cfg.gaps
    ratio = s.max_ratio if max_ratio is None else max_ratio
    limit = s.max_cells if max_cells is None else max_cells
    try:
        report = gap_report(
            ctx, row, col, filters, ratio, exclude_types=[] if all_types else None
        )
    except ToolError as exc:
        raise GapsError(str(exc)) from exc
    m, basis = report.matrix, report.basis
    shown = report.gaps[:limit]
    cells = collect_evidence(
        ctx,
        row,
        col,
        shown,
        basis.filters,
        passages=s.passages_per_cell,
        search=search,
    )
    note = data_note(cfg)
    result = GapsResult(
        row=row,
        col=col,
        filters=basis.filters,
        total_records=m.total_records,
        tagged_on_both=m.total_records - m.untagged_records,
        excluded_types=basis.excluded_types,
        excluded_records=basis.excluded_records,
        max_ratio=ratio,
        min_expected=report.min_expected,
        gaps_found=len(report.gaps),
        cells=cells,
        caveats=[],
        data_note=note.text() if note else None,
        judgement=None,
        seconds=0.0,
        model=None,
        prompt_version=None,
        vocab_version=cfg.vocab.version,
        code_version=code_version(),
        created_at=now(),
    )
    update: dict[str, Any] = {
        "caveats": code_caveats(result, basis.filters.min_confidence)
    }
    if use_llm and cells:
        name = model or cfg.models.agent
        update |= {"model": name, "prompt_version": s.prompt}
        result = result.model_copy(update=update)
        try:
            judgement, dropped, usage = judge(ctx, result, name)
            update |= {"judgement": judgement, "dropped_ids": dropped, "usage": usage}
        except (AgentError, EndpointError) as exc:
            log.warning("gaps: judgement failed: %s", exc)
            update["judgement_error"] = str(exc)
    update["seconds"] = round(time.monotonic() - started, 2)
    result = result.model_copy(update=update)
    log.info(
        "gaps %s x %s: %d gaps, %d judged, %.1f s",
        row.value,
        col.value,
        result.gaps_found,
        len(result.judgement.cells) if result.judgement else 0,
        result.seconds,
    )
    return result


# --- terminal output ------------------------------------------------------------------


def _scope_text(filters: Filters, excluded: list[str]) -> str:
    """The scope in words, e.g. ``year 2015-, unit Sustainable Land Systems``."""
    f = filters.model_dump(exclude_defaults=True)
    f.pop("min_confidence", None)
    if excluded:
        f.pop("pub_types", None)  # stated as "left out" instead
    if not f:
        return "all CDE publications"
    years = ""
    if "year_from" in f or "year_to" in f:
        years = f"years {f.pop('year_from', '')}-{f.pop('year_to', '')}"
    parts = [years] if years else []
    parts += [f"{k}: {', '.join(str(x) for x in v)}" for k, v in f.items()]
    return "; ".join(parts)


def source_numbers(result: GapsResult) -> dict[str, tuple[int, Citation]]:
    """Every evidence record, numbered in order of first appearance."""
    out: dict[str, tuple[int, Citation]] = {}
    for c in result.cells:
        for cit in [*c.records, *(n.citation for n in c.nearby)]:
            if cit.record_id not in out:
                out[cit.record_id] = (len(out) + 1, cit)
    return out


def render_gaps(console: Any, result: GapsResult) -> None:
    """Print the evidence table, the judgement, sources, caveats and footer.

    Args:
        console: A :class:`rich.console.Console`.
        result: The workflow result.
    """
    from rich.markdown import Markdown
    from rich.markup import escape
    from rich.table import Table

    r, c = result.row.value, result.col.value
    console.print(f"[bold]Gaps: {r} x {c}[/bold]", highlight=False)
    console.print(
        f"Scope: {escape(_scope_text(result.filters, result.excluded_types))}. "
        f"{result.total_records:,} matching records; {result.tagged_on_both:,} "
        f"tagged on both {r} and {c} (confidence >= "
        f"{result.filters.min_confidence}).",
        highlight=False,
    )
    console.print(
        f"A gap holds at most {result.max_ratio:g} x the records expected if the "
        f"two dimensions were independent (expected = row total x column total / "
        f"{result.tagged_on_both:,}).",
        highlight=False,
    )
    if not result.cells:
        console.print("No gaps found.")
        for line in result.caveats:
            console.print(f"[dim]- {escape(line)}[/dim]", highlight=False)
        if result.data_note:
            console.print(f"[dim]{escape(result.data_note)}[/dim]", highlight=False)
        return

    numbers = source_numbers(result)
    judged = (
        {(j.row, j.col): j for j in result.judgement.cells} if result.judgement else {}
    )

    def refs(ids: list[str]) -> str:
        return "; ".join(str(numbers[i][0]) for i in ids if i in numbers)

    t = Table(title=None, show_lines=False)
    t.add_column("#", justify="right")
    t.add_column(r)
    t.add_column(c)
    for name in ("Count", "Expected", "Ratio"):
        t.add_column(name, justify="right")
    t.add_column("Verdict")
    t.add_column("Sources")
    for i, cell in enumerate(result.cells, 1):
        j = judged.get((cell.row, cell.col))
        ids = (
            j.evidence
            if j
            else [*cell.record_ids, *(n.citation.record_id for n in cell.nearby)]
        )
        t.add_row(
            str(i),
            escape(cell.row),
            escape(cell.col),
            str(cell.count),
            f"{cell.expected:.1f}",
            f"{cell.ratio:.2f}",
            VERDICT_LABELS[j.verdict] if j else "-",
            refs(ids),
        )
    console.print(t)

    if result.judgement:
        console.print(Markdown(result.judgement.summary))
    elif result.judgement_error:
        console.print(
            f"[red]No judgement: {escape(result.judgement_error)}[/red] The counts "
            "and nearby records above still hold.",
            highlight=False,
        )

    console.print()
    console.print("[bold]Cells[/bold]")
    for i, cell in enumerate(result.cells, 1):
        j = judged.get((cell.row, cell.col))
        head = f"{i}. {escape(cell.row_label)} x {escape(cell.col_label)}"
        if j:
            ref = f" [{refs(j.evidence)}]" if refs(j.evidence) else ""
            console.print(
                f"{head}: [bold]{VERDICT_LABELS[j.verdict]}[/bold]. "
                f"{escape(j.reason)}{escape(ref)}",
                highlight=False,
            )
        else:
            console.print(head, highlight=False)
        if cell.search_error:
            console.print(
                f"   [yellow]Search failed: {escape(cell.search_error)}[/yellow]",
                highlight=False,
            )
        for n in cell.nearby:
            tags = (
                f"{r}: {', '.join(n.row_terms) or '-'}; {c}: "
                f"{', '.join(n.col_terms) or '-'}"
            )
            console.print(
                f"   [dim]nearby [{numbers[n.citation.record_id][0]}] "
                f"{escape(tags)}[/dim]",
                highlight=False,
            )

    console.print()
    console.print("[bold]Sources[/bold]")
    for n, cit in numbers.values():
        year = f" ({cit.year})" if cit.year else ""
        links = []
        if cit.doi:
            url = f"https://doi.org/{cit.doi}"
            links.append(f"[link={url}]{url}[/link]")
        links.append(f"BORIS: [link={cit.boris_url}]{cit.boris_url}[/link]")
        console.print(
            f"[{n}] {escape(cit.title)}{year}. {' | '.join(links)}", highlight=False
        )

    console.print()
    console.print("[bold]Caveats[/bold]")
    for line in result.caveats:
        console.print(f"- {escape(line)}", highlight=False)
    for line in result.judgement.caveats if result.judgement else []:
        console.print(f"- {escape(line)} [dim](model)[/dim]", highlight=False)
    if result.dropped_ids:
        console.print(
            "[red]- The model cited records outside the evidence; they were "
            f"dropped: {escape(', '.join(result.dropped_ids))}[/red]",
            highlight=False,
        )
    if result.data_note:
        console.print(f"[dim]{escape(result.data_note)}[/dim]", highlight=False)
    parts = [
        result.model or "no model",
        result.prompt_version or "-",
        f"vocab {result.vocab_version}",
        f"{result.seconds:.1f} s",
    ]
    if result.usage.get("total_tokens"):
        parts.append(f"{result.usage['total_tokens']:,} tokens")
    console.print(f"[dim]{' | '.join(parts)}[/dim]", highlight=False)
