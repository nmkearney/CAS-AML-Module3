"""``chatcde tag``: tag records against the closed vocabulary, batch by batch.

For each record and each batch of dimensions (``tagging.batches``), the model in
the ``tagging`` role reads the record (title, metadata, abstract, first pages)
and answers in the batch's JSON schema (:mod:`chatcde.tag.answer`) with codes,
quotes and an evidence strength. Code then (decision 0028):

* drops repeated terms and keeps at most ``tagging.max_per_dimension`` per
  dimension; ``other`` needs a label, actors need a role;
* maps the strength to a confidence (``tagging.confidence``); for a record with
  only metadata (no abstract, no page text), a tag that is not ``explicit`` is
  capped at ``tagging.metadata_only_cap``, because a title rarely supports an
  inference, except in ``tagging.metadata_inferable`` (sub-areas, country,
  setting), which titles show well (pilots 1 and 2, decision 0028);
* looks for each quote in the abstract, the page text and the metadata, and sets
  ``Tag.source`` to the part it was found in. A quote found nowhere caps the
  tag's confidence at ``tagging.unverified_cap``, below the 0.5 that counts, so
  the tag stays visible but does not count;
* writes the tags to ``tag_runs`` and one state row per record and batch to
  ``tag_state``, replacing that record and batch in one transaction.

A record and batch already tagged ``ok`` with the same model, prompt and
vocabulary version are skipped, so the stage can be stopped and resumed;
errors are retried on the next run. Requests run in parallel up to
``llm.max_concurrency``.

Publishing a run to the shared ``tags`` table (with derived region and
impact_area) is :func:`chatcde.tag.derive.publish`.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from chatcde.config import Config
from chatcde.harvest.common import code_version, now
from chatcde.schemas import ActorRole, Dimension, Provenance, TextSource
from chatcde.store.endpoint import EndpointClient
from chatcde.tag import tables
from chatcde.tag.answer import OTHER, batch_model, codes_for
from chatcde.tag.extract import RecordText, build_texts, evidence_in
from chatcde.tag.llm import chat_json, load_prompt, run_parallel
from chatcde.tag.prompt import system_prompt
from chatcde.tag.regions import load_countries
from chatcde.tag.vocabulary import load_vocabulary

log = logging.getLogger(__name__)

Scope = Literal["pilot", "ids", "all"]


class TagRunSummary(BaseModel):
    """What one ``chatcde tag`` run did, with its denominators."""

    run: str
    model: str
    prompt_version: str
    vocab_version: str
    scope: str
    records: int  # records in scope
    batches: list[str]
    jobs: int  # record x batch pairs in scope
    done: int  # tagged in this run
    failed: int  # in this run
    skipped: int  # already done
    remaining: int  # left by --limit or a stop
    stopped: bool
    tags_by_dimension: dict[str, int]  # whole run, all records in scope
    unverified_share: float | None  # tags whose quote was not found
    other_share: float | None
    unused_terms: dict[str, int]  # codes never used, per dimension (whole run)


@dataclass(frozen=True)
class Job:
    """One request: a record and a batch."""

    record: RecordText
    batch: str


def scope_ids(
    con: duckdb.DuckDBPyConnection, scope: Scope, ids: list[str] | None = None
) -> list[str]:
    """Record IDs to tag: the pilot draw, the given IDs, or every record.

    Raises:
        RuntimeError: ``pilot`` was asked for but no pilot has been drawn.
        ValueError: Some given IDs are not in the catalogue.
    """
    if scope == "pilot":
        tables.ensure(con, "tag_pilot")
        out = [
            r[0]
            for r in con.execute(
                "SELECT record_id FROM tag_pilot ORDER BY record_id"
            ).fetchall()
        ]
        if not out:
            raise RuntimeError("no pilot drawn yet")
        return out
    if scope == "ids":
        wanted = list(dict.fromkeys(ids or []))
        known = {
            r[0]
            for r in con.execute(
                "SELECT record_id FROM records WHERE list_contains(?, record_id)",
                [wanted],
            ).fetchall()
        }
        if missing := [i for i in wanted if i not in known]:
            raise ValueError(f"not in the catalogue: {', '.join(missing[:5])}")
        return wanted
    return [
        r[0]
        for r in con.execute(
            "SELECT record_id FROM records ORDER BY record_id"
        ).fetchall()
    ]


def locate(evidence: str, record: RecordText) -> TextSource | None:
    """The part of the input a quote comes from, or None if it is in none of them.

    The abstract is checked first, then the page text, then the metadata (title,
    keywords, …), using the same tolerant match as open extraction
    (:func:`chatcde.tag.extract.evidence_in`).
    """
    if record.abstract and evidence_in(evidence, record.abstract):
        return TextSource(record.abstract_source or TextSource.ABSTRACT_BORIS.value)
    if record.fulltext and evidence_in(evidence, record.fulltext):
        return TextSource.FULLTEXT
    if record.metadata and evidence_in(evidence, record.metadata):
        return TextSource.METADATA
    return None


def fallback_source(record: RecordText) -> TextSource:
    """The richest part of the input, for tags whose quote was not found."""
    if record.fulltext:
        return TextSource.FULLTEXT
    if record.abstract:
        return TextSource(record.abstract_source or TextSource.ABSTRACT_BORIS.value)
    return TextSource.METADATA


def to_tags(
    answer: BaseModel,
    record: RecordText,
    *,
    batch: str,
    dimensions: list[str],
    cfg: Config,
    provenance: Provenance,
    run: str,
) -> tuple[list[tables.TagRunRow], int]:
    """Turn one answer into tag rows; return them and the number unverified."""
    s = cfg.tagging
    rows: list[tables.TagRunRow] = []
    unverified = 0
    metadata_only = not record.abstract and not record.fulltext
    for dim in dimensions:
        seen: set[tuple[str, str | None]] = set()
        kept = 0
        for item in getattr(answer, dim):
            term = item.term
            role = getattr(item, "role", None)
            other_label = (getattr(item, "other_label", None) or "").strip() or None
            if term == OTHER and not other_label:
                continue  # an "other" without a label says nothing
            if term != OTHER:
                other_label = None
            key = (term if term != OTHER else f"{OTHER}:{other_label}", role)
            if key in seen or kept >= s.limit(dim):
                continue
            seen.add(key)
            kept += 1
            evidence = item.evidence.strip() or None
            source = locate(evidence, record) if evidence else None
            confidence = s.confidence[item.strength]
            if (
                metadata_only
                and item.strength != "explicit"
                and dim not in s.metadata_inferable
            ):
                confidence = min(confidence, s.metadata_only_cap)
            if source is None:
                unverified += 1
                confidence = min(confidence, s.unverified_cap)
                source = fallback_source(record)
            rows.append(
                tables.TagRunRow(
                    record_id=record.record_id,
                    dimension=Dimension(dim),
                    term=term,
                    other_label=other_label,
                    role=ActorRole(role) if role else None,
                    confidence=confidence,
                    evidence=evidence,
                    source=source,
                    provenance=provenance,
                    run=run,
                    batch=batch,
                )
            )
    return rows, unverified


def _save(
    con: duckdb.DuckDBPyConnection,
    state: tables.TagStateRow,
    rows: list[tables.TagRunRow],
) -> None:
    """Replace one record's tags and state for a run and batch, atomically."""
    key = [state.record_id, state.run, state.batch]
    con.execute("BEGIN TRANSACTION")
    try:
        for table in ("tag_runs", "tag_state"):
            con.execute(
                f"DELETE FROM {table} WHERE record_id = ? AND run = ? AND batch = ?",
                key,
            )
        tables.insert(con, "tag_runs", rows)
        tables.insert(con, "tag_state", [state])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def tag_records(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    client: EndpointClient,
    *,
    scope: Scope = "all",
    ids: list[str] | None = None,
    batches: list[str] | None = None,
    run: str | None = None,
    alternative: bool = False,
    force: bool = False,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> TagRunSummary:
    """Tag the records in scope for every chosen batch not yet done.

    Args:
        con: Open catalogue connection (read-write).
        cfg: Configuration; uses ``tagging``, ``models``, ``vocab`` and ``llm``.
        client: Endpoint client.
        scope: ``pilot`` (the pilot draw), ``ids`` (``ids``) or ``all``.
        ids: Record IDs for ``scope="ids"``.
        batches: Batch names to run (default: all in ``tagging.batches``).
        run: Run name (default ``tagging.run``; ``alt`` with ``alternative``).
        alternative: Use ``model_alternatives.tagging`` instead of
            ``models.tagging``.
        force: Retag record and batch pairs that are already done.
        limit: Send at most this many requests (record x batch) in this run.
        progress: Called with (done, total) after each request.

    Raises:
        ValueError: An unknown batch, or no alternative model is configured.
        RuntimeError: ``scope="pilot"`` without a pilot draw.
    """
    s = cfg.tagging
    chosen = batches or list(s.batches)
    if unknown := [b for b in chosen if b not in s.batches]:
        raise ValueError(f"unknown batch(es): {', '.join(unknown)}")
    if alternative:
        if not cfg.model_alternatives.tagging:
            raise ValueError("no model_alternatives.tagging configured")
        model = cfg.model_alternatives.tagging
    else:
        model = cfg.models.tagging
    run = run or ("alt" if alternative else s.run)
    vocab = load_vocabulary(cfg.vocab.file)
    vocab_version = str(cfg.vocab.version)
    countries = load_countries(s.m49_file)
    template = load_prompt(cfg.prompts.dir, s.prompt)
    schemas = {b: batch_model(b, s.batches[b], vocab, countries, s) for b in chosen}
    systems = {b: system_prompt(template, s.batches[b], vocab, s) for b in chosen}
    tables.ensure(con, "tag_runs", "tag_state")

    record_ids = scope_ids(con, scope, ids)
    done = {
        (r[0], r[1])
        for r in con.execute(
            "SELECT record_id, batch FROM tag_state WHERE status = 'ok' AND run = ? "
            "AND model = ? AND prompt_version = ? AND vocab_version = ?",
            [run, model, s.prompt, vocab_version],
        ).fetchall()
    }
    pairs = [(r, b) for r in record_ids for b in chosen]
    todo = pairs if force else [p for p in pairs if p not in done]
    skipped = len(pairs) - len(todo)
    work = todo[:limit] if limit is not None else todo
    texts = build_texts(
        con,
        sorted({r for r, _ in work}),
        max_fulltext_chars=s.max_fulltext_chars,
        max_input_chars=s.max_input_chars,
    )
    version = code_version()
    log.info(
        "Tagging %d of %d record x batch pairs with %s (%s, vocabulary %s, run %s); "
        "%d already done",
        len(work),
        len(pairs),
        model,
        s.prompt,
        vocab_version,
        run,
        skipped,
    )

    def ask(job: Job) -> BaseModel:
        return chat_json(
            client,
            model=model,
            system=systems[job.batch],
            user=job.record.text,
            schema=schemas[job.batch],
            max_tokens=s.max_tokens,
            extra_body=s.extra_body,
        )

    counts = Counter[str]()

    def handle(job: Job, answer: BaseModel | None, error: str | None) -> None:
        provenance = Provenance(
            code_version=version,
            created_at=now(),
            model=model,
            prompt_version=s.prompt,
            vocab_version=vocab_version,
        )
        rows: list[tables.TagRunRow] = []
        unverified = 0
        if answer is not None:
            rows, unverified = to_tags(
                answer,
                job.record,
                batch=job.batch,
                dimensions=s.batches[job.batch],
                cfg=cfg,
                provenance=provenance,
                run=run,
            )
            counts["done"] += 1
        else:
            counts["failed"] += 1
            log.warning("%s / %s: %s", job.record.record_id, job.batch, error)
        state = tables.TagStateRow(
            record_id=job.record.record_id,
            run=run,
            batch=job.batch,
            model=model,
            prompt_version=s.prompt,
            vocab_version=vocab_version,
            status="error" if error else "ok",
            n_tags=len(rows),
            n_unverified=unverified,
            sources=job.record.sources,
            input_chars=len(job.record.text),
            error=error[:500] if error else None,
            code_version=version,
            created_at=provenance.created_at,
        )
        _save(con, state, rows)
        counts["finished"] += 1
        if progress:
            progress(counts["finished"], len(work))

    stopped = run_parallel(
        [Job(texts[r], b) for r, b in work if r in texts],
        ask,
        handle,
        workers=cfg.llm.max_concurrency,
        max_consecutive_failures=s.max_consecutive_failures,
    )
    totals = run_totals(con, run, record_ids, chosen, cfg, vocab, countries)
    return TagRunSummary(
        run=run,
        model=model,
        prompt_version=s.prompt,
        vocab_version=vocab_version,
        scope=scope,
        records=len(record_ids),
        batches=chosen,
        jobs=len(pairs),
        done=counts["done"],
        failed=counts["failed"],
        skipped=skipped,
        remaining=len(todo) - counts["done"] - counts["failed"],
        stopped=stopped,
        **totals,
    )


def run_totals(
    con: duckdb.DuckDBPyConnection,
    run: str,
    record_ids: list[str],
    batches: list[str],
    cfg: Config,
    vocab: dict[str, Any],
    countries: dict[str, Any],
) -> dict[str, Any]:
    """Tags per dimension, unverified and ``other`` shares, unused codes (in scope)."""
    dims = [d for b in batches for d in cfg.tagging.batches[b]]
    rows = con.execute(
        "SELECT dimension, term FROM tag_runs "
        "WHERE run = ? AND list_contains(?, record_id) AND list_contains(?, dimension)",
        [run, record_ids, dims],
    ).fetchall()
    by_dim = Counter(d for d, _ in rows)
    used: dict[str, set[str]] = {}
    for d, term in rows:
        used.setdefault(d, set()).add(term)
    n = len(rows)
    unverified = con.execute(
        "SELECT sum(n_unverified), sum(n_tags) FROM tag_state WHERE run = ? "
        "AND list_contains(?, record_id) AND list_contains(?, batch)",
        [run, record_ids, batches],
    ).fetchone()
    unused = {}
    for d in dims:
        if d == Dimension.COUNTRY.value:
            continue  # most countries are never studied; not a vocabulary signal
        codes = [c for c in codes_for(d, vocab, countries) if c != OTHER]
        unused[d] = sum(1 for c in codes if c not in used.get(d, set()))
    return {
        "tags_by_dimension": {d: by_dim.get(d, 0) for d in dims},
        "unverified_share": (
            (unverified[0] or 0) / unverified[1]
            if unverified and unverified[1]
            else None
        ),
        "other_share": (sum(1 for _, t in rows if t == OTHER) / n) if n else None,
        "unused_terms": unused,
    }
