"""Step 2 of ``chatcde vocab extract``: free-text labels for each sampled record.

The model in the ``tagging`` role reads each sampled record (title, keywords,
abstract and the first pages of parsed text) and writes short English labels for
six kinds: methods, themes, data types, settings, systems and actors (with a
role). These are the strategy's open questions: the dimensions whose seed lists
were proposed by us rather than taken from a CDE source (decision 0019).

The seed vocabulary is deliberately **not** shown to the model here, so the
labels say what the texts are about rather than echo our draft terms. Each label
carries a quote from the text; the code checks that the quote really occurs in
the input (``evidence_found``), which flags invented evidence.

Results go to ``vocab_labels`` (one row per label) and ``vocab_label_state``
(one row per record, model and prompt version, ``ok`` or ``error``). Records
already labelled ``ok`` by the same model and prompt version are skipped, so the
step can be stopped and resumed; errors are retried on the next run.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import duckdb
from pydantic import BaseModel, Field

from chatcde.config import Config
from chatcde.harvest.common import code_version, now
from chatcde.store.endpoint import EndpointClient
from chatcde.tag import tables
from chatcde.tag.llm import chat_json, fill_prompt, load_prompt, run_parallel

log = logging.getLogger(__name__)

#: Field of :class:`ExtractedLabels` -> label kind (= vocabulary dimension name).
KINDS: dict[str, str] = {
    "methods": "method",
    "themes": "theme",
    "data_types": "data_type",
    "settings": "setting",
    "systems": "system",
    "actors": "actors",
}


class LabelItem(BaseModel):
    """One free-text label with its supporting quote."""

    label: str = Field(description="Short English noun phrase, 1-6 words")
    evidence: str = Field(description="Quote from the text, original language")


class ActorItem(LabelItem):
    """A group of actors and how they relate to the research."""

    role: Literal["studied", "co_producer", "audience"]


class ExtractedLabels(BaseModel):
    """The model's answer for one record (the JSON schema sent to the server)."""

    methods: list[LabelItem]
    themes: list[LabelItem]
    data_types: list[LabelItem]
    settings: list[LabelItem]
    systems: list[LabelItem]
    actors: list[ActorItem]


class LabelSummary(BaseModel):
    """What the label step did, with its denominators."""

    model: str
    prompt_version: str
    sampled: int
    labelled: int  # in this run
    failed: int  # in this run
    skipped: int  # already done
    remaining: int  # left by --limit or a stop
    stopped: bool  # stopped because the endpoint kept failing
    ok_total: int  # records labelled ok with this model and prompt
    labels_by_kind: dict[str, int]
    evidence_found_share: float | None


@dataclass(frozen=True)
class RecordText:
    """The text the model sees for one record, and its parts.

    ``text`` is what is sent. The parts (as included in ``text``, before the
    overall cut) let tagging find which part a quote came from (decision 0028).
    """

    record_id: str
    text: str
    sources: str  # e.g. "abstract,fulltext"
    metadata: str = ""  # title, year, type, language, keywords, container
    abstract: str = ""
    abstract_source: str | None = None  # TextSource value, e.g. "abstract_boris"
    fulltext: str = ""


#: A role the model sometimes repeats inside an actor label: "companies (audience)".
_ROLE_SUFFIX = re.compile(
    r"\s*[(\[]\s*(studied|co[_ -]?producers?|audience)\s*[)\]]$", re.I
)


def normalise(text: str) -> str:
    """Lower case, NFKC, whitespace collapsed: for comparing labels and quotes."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _letters(text: str) -> str:
    """Only the letters and digits of ``text``, lower case (NFKC, soft hyphens dropped).

    PDF text breaks words at line ends ("spec- trometry", "laborato ry"), and
    the model rejoins them when it quotes, so quotes are compared without any
    spaces or punctuation at all.
    """
    return "".join(c for c in normalise(text) if c.isalnum())


def evidence_in(evidence: str, text: str, *, min_chars: int = 12) -> bool:
    """Whether the quote occurs in the text, ignoring case, spacing and punctuation.

    An ellipsis in the quote ("..." or "…") splits it; every part with at least
    ``min_chars`` letters must occur (shorter parts are too common to prove
    anything). A quote with no part that long counts as found only if it occurs
    as a whole.
    """
    hay = _letters(text)
    parts = [p for p in (_letters(x) for x in re.split(r"\.\.\.|…", evidence)) if p]
    if not parts:
        return False
    long_parts = [p for p in parts if len(p) >= min_chars]
    if not long_parts:
        return _letters(evidence) in hay
    return all(p in hay for p in long_parts)


def build_texts(
    con: duckdb.DuckDBPyConnection,
    record_ids: list[str],
    *,
    max_fulltext_chars: int,
    max_input_chars: int,
) -> dict[str, RecordText]:
    """Assemble the model input for each record.

    Title, year, type, language, keywords and container, then the abstract, then
    page text in page order up to ``max_fulltext_chars`` (only if the ``pages``
    table exists), all cut to ``max_input_chars``.
    """
    if not record_ids:
        return {}
    rows = con.execute(
        """
        SELECT record_id, title, year, pub_type, languages, keywords, container,
               abstract, abstract_source
        FROM records WHERE list_contains(?, record_id)
        """,
        [record_ids],
    ).fetchall()
    pages: dict[str, list[str]] = {}
    if max_fulltext_chars > 0 and tables.has_table(con, "pages"):
        for rid, text in con.execute(
            "SELECT record_id, text FROM pages WHERE list_contains(?, record_id) "
            "ORDER BY record_id, page",
            [record_ids],
        ).fetchall():
            pages.setdefault(rid, []).append(text)
    out: dict[str, RecordText] = {}
    for rid, title, year, pub_type, langs, keywords, container, abstract, a_src in rows:
        head = [f"Title: {title}"]
        meta = [
            f"Year: {year}" if year else None,
            f"Type: {pub_type}" if pub_type else None,
            f"Language: {', '.join(langs)}" if langs else None,
        ]
        head.append(" | ".join(m for m in meta if m))
        if keywords:
            head.append(f"Keywords: {'; '.join(keywords)}")
        if container:
            head.append(f"Published in: {container}")
        meta_text = "\n".join(h for h in head if h)
        parts = [meta_text]
        sources = []
        abstract_text = (abstract or "").strip()
        if abstract_text:
            parts.append("Abstract:\n" + abstract_text)
            sources.append("abstract")
        body = "\n\n".join(t.strip() for t in pages.get(rid, []) if t.strip())
        body = body[:max_fulltext_chars]
        if body:
            parts.append("Full text (first pages):\n" + body)
            sources.append("fulltext")
        text = "\n\n".join(parts)[:max_input_chars]
        out[rid] = RecordText(
            rid,
            text,
            ",".join(sources) or "metadata",
            metadata=meta_text,
            abstract=abstract_text,
            abstract_source=a_src,
            fulltext=body,
        )
    return out


def to_rows(
    answer: ExtractedLabels,
    record: RecordText,
    *,
    limit: int,
    model: str,
    prompt_version: str,
    version: str,
) -> list[tables.LabelRow]:
    """Turn one answer into label rows: at most ``limit`` per kind, no duplicates."""
    stamp = now()
    rows: list[tables.LabelRow] = []
    for field, kind in KINDS.items():
        seen: set[str] = set()
        for item in getattr(answer, field)[:limit]:
            label = re.sub(r"\s+", " ", item.label).strip()
            label = _ROLE_SUFFIX.sub("", label).strip()
            norm = normalise(label)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            evidence = item.evidence.strip() or None
            rows.append(
                tables.LabelRow(
                    record_id=record.record_id,
                    kind=kind,
                    label=label,
                    label_norm=norm,
                    evidence=evidence,
                    evidence_found=bool(evidence)
                    and evidence_in(evidence, record.text),
                    role=getattr(item, "role", None),
                    model=model,
                    prompt_version=prompt_version,
                    code_version=version,
                    created_at=stamp,
                )
            )
    return rows


def _save(
    con: duckdb.DuckDBPyConnection,
    record: RecordText,
    state: tables.LabelStateRow,
    rows: list[tables.LabelRow],
) -> None:
    """Replace one record's labels and state for this model and prompt, atomically."""
    key = [record.record_id, state.model, state.prompt_version]
    con.execute("BEGIN TRANSACTION")
    try:
        for table in ("vocab_labels", "vocab_label_state"):
            con.execute(
                f"DELETE FROM {table} WHERE record_id = ? AND model = ? "
                "AND prompt_version = ?",
                key,
            )
        tables.insert(con, "vocab_labels", rows)
        tables.insert(con, "vocab_label_state", [state])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def label_sample(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    client: EndpointClient,
    *,
    force: bool = False,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> LabelSummary:
    """Label every sampled record that is not yet labelled with this model and prompt.

    Up to ``llm.max_concurrency`` requests run in parallel; results are written
    from this thread only (DuckDB connections are not shared between threads).

    Args:
        con: Open catalogue connection (read-write).
        cfg: Configuration; uses ``models.tagging``, ``vocab_extract`` and ``llm``.
        client: Endpoint client.
        force: Relabel records that are already done.
        limit: Label at most this many records in this run.
        progress: Called with (done, total) after each record.

    Raises:
        RuntimeError: There is no sample yet (run the sample step first).
    """
    s = cfg.vocab_extract
    model = cfg.models.tagging
    prompt_version = s.extract_prompt
    system = fill_prompt(
        load_prompt(cfg.prompts.dir, prompt_version),
        {"labels_per_kind": s.labels_per_kind},
    )
    tables.ensure(con, "vocab_sample", "vocab_labels", "vocab_label_state")
    sampled = [
        r[0]
        for r in con.execute(
            "SELECT record_id FROM vocab_sample ORDER BY record_id"
        ).fetchall()
    ]
    if not sampled:
        raise RuntimeError("no sample yet: run `chatcde vocab extract --step sample`")
    done = {
        r[0]
        for r in con.execute(
            "SELECT record_id FROM vocab_label_state WHERE status = 'ok' "
            "AND model = ? AND prompt_version = ?",
            [model, prompt_version],
        ).fetchall()
    }
    todo = sampled if force else [r for r in sampled if r not in done]
    skipped = len(sampled) - len(todo)
    work = todo[:limit] if limit is not None else todo
    texts = build_texts(
        con,
        work,
        max_fulltext_chars=s.max_fulltext_chars,
        max_input_chars=s.max_input_chars,
    )
    version = code_version()
    log.info(
        "Labelling %d of %d sampled records with %s (%s); %d already done",
        len(work),
        len(sampled),
        model,
        prompt_version,
        skipped,
    )

    def ask(record: RecordText) -> ExtractedLabels:
        return chat_json(
            client,
            model=model,
            system=system,
            user=record.text,
            schema=ExtractedLabels,
            max_tokens=s.max_tokens,
            extra_body=s.extra_body,
        )

    counts = {"labelled": 0, "failed": 0, "finished": 0}

    def handle(
        rec: RecordText, answer: ExtractedLabels | None, error: str | None
    ) -> None:
        rows = (
            to_rows(
                answer,
                rec,
                limit=s.labels_per_kind,
                model=model,
                prompt_version=prompt_version,
                version=version,
            )
            if answer is not None
            else []
        )
        if error:
            counts["failed"] += 1
            log.warning("%s: %s", rec.record_id, error)
        else:
            counts["labelled"] += 1
        state = tables.LabelStateRow(
            record_id=rec.record_id,
            model=model,
            prompt_version=prompt_version,
            status="error" if error else "ok",
            n_labels=len(rows),
            sources=rec.sources,
            input_chars=len(rec.text),
            error=error[:500] if error else None,
            code_version=version,
            created_at=now(),
        )
        _save(con, rec, state, rows)
        counts["finished"] += 1
        if progress:
            progress(counts["finished"], len(work))

    stopped = run_parallel(
        [texts[r] for r in work if r in texts],
        ask,
        handle,
        workers=cfg.llm.max_concurrency,
        max_consecutive_failures=s.max_consecutive_failures,
    )
    labelled, failed = counts["labelled"], counts["failed"]

    ok_total, by_kind, found = _totals(con, model, prompt_version)
    return LabelSummary(
        model=model,
        prompt_version=prompt_version,
        sampled=len(sampled),
        labelled=labelled,
        failed=failed,
        skipped=skipped,
        remaining=len(todo) - labelled - failed,
        stopped=stopped,
        ok_total=ok_total,
        labels_by_kind=by_kind,
        evidence_found_share=found,
    )


def _totals(
    con: duckdb.DuckDBPyConnection, model: str, prompt_version: str
) -> tuple[int, dict[str, int], float | None]:
    """Records labelled ok, labels per kind and share of quotes found (sample only)."""
    ok = con.execute(
        "SELECT count(*) FROM vocab_label_state s JOIN vocab_sample USING (record_id) "
        "WHERE status = 'ok' AND model = ? AND prompt_version = ?",
        [model, prompt_version],
    ).fetchone()[0]  # type: ignore[index]
    rows = con.execute(
        "SELECT kind, evidence_found FROM vocab_labels "
        "JOIN vocab_sample USING (record_id) "
        "WHERE model = ? AND prompt_version = ?",
        [model, prompt_version],
    ).fetchall()
    by_kind = Counter(k for k, _ in rows)
    ordered = {k: by_kind.get(k, 0) for k in KINDS.values()}
    share = round(sum(f for _, f in rows) / len(rows), 3) if rows else None
    return int(ok), ordered, share
