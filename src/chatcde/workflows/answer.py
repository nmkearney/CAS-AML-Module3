"""Show the agent's answers in the terminal: numbered sources and a data note.

Used by ``chatcde ask`` and ``chatcde chat`` (issue #26, decision 0023):

* :func:`number_citations` turns the model's ``[BORIS:<id>, p. 4]`` references
  into short numbered ones (``[1, p. 4]``), numbered in order of first use.
* :func:`render_result` prints the answer as Markdown, then the sources with
  title, year, DOI and BORIS links (clickable in terminals that support it),
  then warnings: cited IDs that are not in the catalogue, records no tool
  returned, a used-up tool budget or a cut-off answer.
* :func:`data_note` says which data edition answered and how much of it has
  full text, so readers know what the evidence could have covered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from chatcde import catalogue
from chatcde.agent import CITE_GROUP_RE, CITE_RE, AgentResult, ToolCallRecord
from chatcde.config import Config
from chatcde.schemas import BundleManifest, Edition

#: File in ``data.dir`` that a downloaded bundle carries (``chatcde fetch-data``).
MANIFEST_NAME = "manifest.json"


def number_citations(result: AgentResult) -> str:
    """The answer with each ``[BORIS:...]`` group replaced by numbered references.

    Records are numbered in the order of :attr:`AgentResult.citations` (first
    use); IDs not in the catalogue become ``?``.
    """
    numbers = {c.citation.record_id: i for i, c in enumerate(result.citations, 1)}

    def replace(group: re.Match[str]) -> str:
        parts = []
        for m in CITE_RE.finditer(group.group(1)):
            n = numbers.get(m.group(1).rstrip(".,;"))
            label = str(n) if n else "?"
            parts.append(f"{label}, p. {m.group(2)}" if m.group(2) else label)
        return "[" + "; ".join(parts) + "]" if parts else group.group(0)

    return CITE_GROUP_RE.sub(replace, result.answer)


@dataclass(frozen=True)
class DataNote:
    """Which data the answer rests on."""

    edition: Edition
    bundle_version: str | None
    records: int
    with_fulltext: int
    abstract_only: int
    metadata_only: int

    def text(self) -> str:
        """One or two plain sentences for the footer."""
        source = (
            f"bundle {self.bundle_version}" if self.bundle_version else "local build"
        )
        line = (
            f"Data: {self.edition.value} edition ({source}); {self.records:,} "
            f"publications: {self.with_fulltext:,} with full text, "
            f"{self.abstract_only:,} abstract only, {self.metadata_only:,} "
            "metadata only."
        )
        if self.edition is Edition.OPEN:
            line += (
                " Restricted full texts are not included: for those records the "
                "answer rests on abstracts and metadata."
            )
        return line


def _has_rows(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """True if ``table`` exists and holds at least one row."""
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()
    if not row or not row[0]:
        return False
    return con.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone() == (True,)


def data_note(cfg: Config) -> DataNote | None:
    """Edition and text coverage of the local data, or ``None`` without a catalogue.

    The edition comes from ``manifest.json`` in ``data.dir`` (a downloaded
    bundle); without one the data was built locally and holds every full text
    the builder could download, like the full edition (decision 0004). A record
    has full text when it has parsed pages, or, before ``chatcde parse`` ran,
    when its file was downloaded (``has_fulltext``).
    """
    edition, version = Edition.FULL, None
    manifest = cfg.data.dir / MANIFEST_NAME
    if manifest.is_file():
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        m = BundleManifest.model_validate(raw)
        edition, version = m.edition, m.version
    path: Path = cfg.data.catalogue
    if not path.is_file():
        return None
    con = catalogue.connect(path, read_only=True)
    try:
        if not _has_rows(con, "records"):
            return DataNote(edition, version, 0, 0, 0, 0)
        fulltext = (
            "record_id IN (SELECT DISTINCT record_id FROM pages)"
            if _has_rows(con, "pages")
            else "has_fulltext"
        )
        row = con.execute(
            f"SELECT count(*), count(*) FILTER ({fulltext}), "
            f"count(*) FILTER (NOT ({fulltext}) AND coalesce(abstract, '') <> '') "
            "FROM records"
        ).fetchone()
    finally:
        con.close()
    total, full, abstract = (int(x) for x in row) if row else (0, 0, 0)
    return DataNote(edition, version, total, full, abstract, total - full - abstract)


def tool_line(rec: ToolCallRecord) -> str:
    """One progress line for a tool call, e.g. ``search_publications(query=...)``."""
    args = ", ".join(
        f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in rec.arguments.items()
    )
    if len(args) > 100:
        args = args[:97] + "..."
    status = f"ok, {rec.seconds:.1f} s" if rec.ok else f"error: {rec.error}"
    return f"  -> {rec.name}({args})  [{status}]"


def render_result(
    console: Console, result: AgentResult, *, footer: bool = True
) -> None:
    """Print an answer with numbered sources, warnings and a short footer."""
    console.print(Markdown(number_citations(result) or "_(no answer)_"))
    if result.citations:
        console.print()
        console.print("[bold]Sources[/bold]")
        for i, c in enumerate(result.citations, 1):
            cit = c.citation
            year = f" ({cit.year})" if cit.year else ""
            links = []
            if cit.doi:
                url = f"https://doi.org/{cit.doi}"
                links.append(f"[link={url}]{url}[/link]")
            links.append(f"BORIS: [link={cit.boris_url}]{cit.boris_url}[/link]")
            pages = f" pp. {', '.join(str(p) for p in c.pages)}." if c.pages else ""
            flag = (
                "" if c.from_tools else " [yellow](not returned by any tool)[/yellow]"
            )
            console.print(
                f"[{i}] {escape(cit.title)}{year}.{pages} {' | '.join(links)}{flag}",
                highlight=False,
            )
    if result.unknown_ids:
        console.print(
            "[red]Cited IDs not in the catalogue (treat those claims as "
            f"unsupported): {escape(', '.join(result.unknown_ids))}[/red]",
            highlight=False,
        )
    if result.stop_reason == "budget":
        console.print(
            f"[yellow]The tool-call budget ({len(result.tool_calls)} calls) was used "
            "up; the answer may be incomplete.[/yellow]"
        )
    elif result.stop_reason == "length":
        console.print("[yellow]The answer was cut off (agent.max_tokens).[/yellow]")
    if footer:
        tokens = result.usage.get("total_tokens")
        parts = [
            result.model,
            result.prompt_version,
            f"{len(result.tool_calls)} tool calls",
            f"{result.seconds:.1f} s",
        ]
        if tokens:
            parts.append(f"{tokens:,} tokens")
        console.print(f"[dim]{' | '.join(parts)}[/dim]", highlight=False)
