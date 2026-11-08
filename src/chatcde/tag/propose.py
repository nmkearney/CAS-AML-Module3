"""Step 3 of ``chatcde vocab extract``: cluster the labels and propose terms.

1. **Embed** every distinct normalised label of a kind with the model in the
   ``embedding`` role. Vectors are cached in ``vocab_label_vectors``.
2. **Cluster** them by average-linkage agglomerative clustering on cosine
   similarity, merging until the closest pair of clusters is less similar than
   ``vocab_extract.cluster_threshold``. Average linkage avoids the long chains
   that single linkage (or connected components over a threshold) builds from
   labels that are each close to the next (decision 0019).
3. **Count** in code: labels, distinct records and example records per cluster.
   Clusters seen in fewer than ``min_records`` records are listed as
   ``unclustered`` instead of being sent to the model.
4. **Propose**: the ``tagging`` model names each cluster, defines it and says
   whether it matches, splits or adds to the current draft terms. Proposals are
   cached in ``vocab_proposals`` by a hash of the cluster's members, as the
   model gave them; the code's checks (:func:`_check`) run when the file is written.
5. **Write** ``vocab/candidates.yaml``: a header with provenance and the sample
   as denominator, then one section per kind, largest clusters first.

The counts in the file always come from the catalogue, never from the model
(CLAUDE.md, hard rules); the model only names and defines.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import duckdb
import numpy as np
import yaml
from pydantic import BaseModel

from chatcde.config import Config
from chatcde.harvest.common import code_version, now
from chatcde.store.embeddings import prepare_vector
from chatcde.store.endpoint import EndpointClient, EndpointError
from chatcde.tag import tables
from chatcde.tag.extract import KINDS
from chatcde.tag.llm import LLMReplyError, chat_json, fill_prompt, load_prompt
from chatcde.tag.vocabulary import load_vocabulary, seed_terms

log = logging.getLogger(__name__)

#: What each kind means, for the propose prompt.
KIND_DESCRIPTIONS: dict[str, str] = {
    "method": "research methods and approaches used",
    "theme": "topics and problems studied",
    "data_type": "kinds of data used or produced",
    "setting": "kinds of places or environments",
    "system": "social-ecological or economic systems in focus",
    "actors": "groups of people or organisations involved",
}

#: Dimensions a cluster of each kind may be filed under; their draft terms are
#: shown to the model. Themes are the open kind and may go to several dimensions.
TARGET_DIMENSIONS: dict[str, list[str]] = {
    "method": ["method"],
    "theme": ["sub_area", "system", "setting", "justice_lens"],
    "data_type": ["data_type"],
    "setting": ["setting"],
    "system": ["system"],
    "actors": ["actors"],
}

Dimension = Literal[
    "method",
    "sub_area",
    "system",
    "setting",
    "justice_lens",
    "data_type",
    "actors",
    "other",
]


class Proposal(BaseModel):
    """The model's proposal for one cluster (the JSON schema sent to the server)."""

    label: str
    definition: str
    action: Literal["new", "merge_into_existing", "split_existing"]
    existing_term: str | None
    suggested_dimension: Dimension
    note: str | None


class ProposeSummary(BaseModel):
    """What the propose step did, per kind, with its denominators."""

    records: int  # sampled records labelled ok (the denominator in the file)
    labels: dict[str, int]  # label rows per kind
    distinct: dict[str, int]  # distinct normalised labels per kind
    clusters: dict[str, int]  # clusters at the threshold
    candidates: dict[str, int]  # clusters with >= min_records records
    unclustered: dict[str, int]
    proposed: int  # model calls in this run
    cached: int  # proposals reused
    failed: int
    output: str


@dataclass
class LabelStats:
    """One distinct normalised label of a kind, with the records that carry it."""

    norm: str
    surface: Counter[str] = field(default_factory=Counter)  # spellings as written
    records: set[str] = field(default_factory=set)
    evidence: list[str] = field(default_factory=list)
    roles: Counter[str] = field(default_factory=Counter)

    @property
    def shown(self) -> str:
        """The most common spelling."""
        return self.surface.most_common(1)[0][0]


@dataclass
class Cluster:
    """A group of similar labels."""

    kind: str
    members: list[LabelStats]

    @property
    def records(self) -> set[str]:
        """Records that carry any member label."""
        return set().union(*(m.records for m in self.members))

    @property
    def n_labels(self) -> int:
        """Label rows in the cluster (one per record and label)."""
        return sum(len(m.records) for m in self.members)

    def key(self) -> str:
        """Stable hash of the kind and the member labels, for the proposal cache."""
        text = self.kind + "\n" + "\n".join(sorted(m.norm for m in self.members))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def ranked(self) -> list[LabelStats]:
        """Members, most records first (ties by label)."""
        return sorted(self.members, key=lambda m: (-len(m.records), m.norm))


def average_linkage(vectors: np.ndarray, threshold: float) -> list[list[int]]:
    """Cluster rows of ``vectors`` by average linkage on cosine similarity.

    Starts with one cluster per row and repeatedly merges the two clusters with
    the highest mean pairwise similarity, until that is below ``threshold``. The
    similarity of a merged cluster to the others follows the Lance-Williams
    update for average linkage (size-weighted mean of the two rows), so the
    result equals UPGMA. O(n^2) memory, about n merges of O(n^2) each: fine for
    a few thousand labels.

    Returns:
        Clusters as lists of row indices, each sorted; clusters ordered by their
        first index.
    """
    n = len(vectors)
    if n == 0:
        return []
    x = vectors.astype(np.float64)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    x = x / np.where(norms == 0, 1.0, norms)
    sim = x @ x.T
    np.fill_diagonal(sim, -np.inf)
    sizes = np.ones(n)
    members: list[list[int]] = [[i] for i in range(n)]
    while n > 1:
        flat = int(np.argmax(sim))
        i, j = divmod(flat, sim.shape[1])
        if sim[i, j] < threshold:
            break
        si, sj = sizes[i], sizes[j]
        row = (si * sim[i] + sj * sim[j]) / (si + sj)
        sim[i, :] = row
        sim[:, i] = row
        sim[i, i] = -np.inf
        sim[j, :] = -np.inf
        sim[:, j] = -np.inf
        sizes[i] = si + sj
        members[i].extend(members[j])
        members[j] = []
    out = [sorted(m) for m in members if m]
    return sorted(out, key=lambda m: m[0])


def load_label_stats(
    con: duckdb.DuckDBPyConnection, model: str, prompt_version: str
) -> tuple[int, dict[str, list[LabelStats]], dict[str, int]]:
    """Labels of the sampled records, grouped by kind and normalised label.

    Only records labelled ``ok`` by ``model`` with ``prompt_version`` count.

    Returns:
        (records labelled ok, kind -> label stats sorted by label, kind -> label rows)
    """
    n_records = con.execute(
        "SELECT count(*) FROM vocab_label_state JOIN vocab_sample USING (record_id) "
        "WHERE status = 'ok' AND model = ? AND prompt_version = ?",
        [model, prompt_version],
    ).fetchone()[0]  # type: ignore[index]
    rows = con.execute(
        """
        SELECT l.kind, l.label, l.label_norm, l.record_id, l.evidence,
               l.evidence_found, l.role
        FROM vocab_labels l JOIN vocab_sample USING (record_id)
        WHERE l.model = ? AND l.prompt_version = ?
        ORDER BY l.kind, l.label_norm, l.record_id
        """,
        [model, prompt_version],
    ).fetchall()
    stats: dict[str, dict[str, LabelStats]] = defaultdict(dict)
    counts: Counter[str] = Counter()
    for kind, label, norm, rid, evidence, found, role in rows:
        counts[kind] += 1
        st = stats[kind].setdefault(norm, LabelStats(norm))
        st.surface[label] += 1
        st.records.add(rid)
        if evidence and found and len(st.evidence) < 3:
            st.evidence.append(evidence)
        if role:
            st.roles[role] += 1
    grouped = {k: [stats[k][n] for n in sorted(stats[k])] for k in stats}
    return int(n_records), grouped, dict(counts)


def embed_labels(
    con: duckdb.DuckDBPyConnection,
    client: EndpointClient,
    labels: Sequence[str],
    *,
    model: str,
    dimensions: int | None,
    batch_size: int,
) -> dict[str, list[float]]:
    """Vectors for ``labels``, from the cache or the embedding model.

    New vectors are written to ``vocab_label_vectors`` batch by batch, so an
    interrupted run keeps what it has.
    """
    tables.ensure(con, "vocab_label_vectors")
    dims = dimensions or 0
    cached = {
        norm: list(vec)
        for norm, vec in con.execute(
            "SELECT label_norm, vector FROM vocab_label_vectors "
            "WHERE model = ? AND dimensions = ?",
            [model, dims],
        ).fetchall()
    }
    missing = sorted({lab for lab in labels if lab not in cached})
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        vectors = [prepare_vector(v, dimensions) for v in client.embed(model, batch)]
        rows = [
            tables.LabelVectorRow(label_norm=n, model=model, dimensions=dims, vector=v)
            for n, v in zip(batch, vectors, strict=True)
        ]
        tables.insert(con, "vocab_label_vectors", rows)
        cached.update(zip(batch, vectors, strict=True))
    if missing:
        log.info("Embedded %d new labels with %s", len(missing), model)
    return {lab: cached[lab] for lab in labels}


def cluster_kind(
    kind: str,
    stats: list[LabelStats],
    vectors: dict[str, list[float]],
    threshold: float,
) -> list[Cluster]:
    """Cluster one kind's labels; largest clusters (by records) first."""
    if not stats:
        return []
    matrix = np.array([vectors[s.norm] for s in stats], dtype=np.float64)
    groups = average_linkage(matrix, threshold)
    clusters = [Cluster(kind, [stats[i] for i in g]) for g in groups]
    return sorted(clusters, key=lambda c: (-len(c.records), c.ranked()[0].norm))


def _cluster_message(
    cluster: Cluster, seeds: dict[str, list[str]], max_labels: int
) -> str:
    """The user message for one cluster: labels with counts, quotes, draft terms."""
    lines = ["Labels in the cluster (publications):"]
    for m in cluster.ranked()[:max_labels]:
        lines.append(f"- {m.shown} ({len(m.records)})")
    extra = len(cluster.members) - max_labels
    if extra > 0:
        lines.append(f"- ... and {extra} more labels")
    quotes = [q for m in cluster.ranked() for q in m.evidence][:5]
    if quotes:
        lines.append("\nEvidence quotes:")
        lines.extend(f'- "{q}"' for q in quotes)
    lines.append("\nCurrent draft terms:")
    for dim, terms in seeds.items():
        lines.append(f"{dim}:")
        lines.extend(f"- {t}" for t in terms or ["(none yet)"])
    return "\n".join(lines)


def resolve_term(name: str, terms: Sequence[str]) -> str | None:
    """The draft term the model meant by ``name``, or ``None`` if there is none.

    Matches exactly, then ignoring case and spacing, then against the label part
    of ``"code: label"`` terms or the code alone (the model often drops or keeps
    only the code, e.g. "Sustainable land use" for "SL-a: Sustainable land use").
    """
    if name in terms:
        return name

    def key(text: str) -> str:
        return " ".join(text.lower().split())

    wanted = key(name)
    for term in terms:
        code, _, label = term.partition(": ")
        if wanted in {key(term), key(label) if label else None, key(code)}:
            return term
    return None


def _checked(
    proposal: Proposal | None, kind: str, seeds: dict[str, list[str]]
) -> Proposal | None:
    """:func:`_check` for an optional proposal (a failed call has none)."""
    return None if proposal is None else _check(proposal, kind, seeds)


def _check(proposal: Proposal, kind: str, seeds: dict[str, list[str]]) -> Proposal:
    """Fix what code can check: the existing term's spelling, the dimension of a kind.

    Applied when candidates.yaml is written, not before caching, so the cache
    keeps the model's own answer and a better check needs no new model calls.
    """
    note = proposal.note
    existing = proposal.existing_term
    known = [t for terms in seeds.values() for t in terms]
    if existing is not None:
        resolved = resolve_term(existing, known)
        if resolved is None:
            tag = f"[model named unknown term {existing!r}]"
            note = f"{note} {tag}" if note else tag
        existing = resolved
    action = proposal.action
    if action != "new" and existing is None:
        action = "new"
    dim = proposal.suggested_dimension
    if kind != "theme":
        dim = TARGET_DIMENSIONS[kind][0]  # type: ignore[assignment]
    return proposal.model_copy(
        update={
            "existing_term": existing,
            "action": action,
            "note": note,
            "suggested_dimension": dim,
        }
    )


def _candidate(
    cluster: Cluster, proposal: Proposal | None, cfg: Config
) -> dict[str, Any]:
    """One entry of candidates.yaml: proposal fields, then counts from code."""
    s = cfg.vocab_extract
    entry: dict[str, Any] = {}
    if proposal is not None:
        entry.update(proposal.model_dump())
    else:
        entry["label"] = cluster.ranked()[0].shown
        entry["note"] = "[no proposal: the model call failed; re-run to retry]"
    entry["n_records"] = len(cluster.records)
    entry["n_labels"] = cluster.n_labels
    entry["example_labels"] = [
        f"{m.shown} ({len(m.records)})" for m in cluster.ranked()[: s.example_labels]
    ]
    roles = sum((m.roles for m in cluster.members), Counter())
    if roles:
        entry["roles"] = dict(roles.most_common())
    entry["example_record_ids"] = sorted(cluster.records)[: s.example_records]
    return entry


def _unclustered(cluster: Cluster) -> str:
    """A small cluster as one line, ``"n: label (n); label (n)"``, for skimming.

    Record IDs are left out to keep the committed file small; they are in
    ``vocab_labels`` (query by ``label_norm``).
    """
    labels = "; ".join(f"{m.shown} ({len(m.records)})" for m in cluster.ranked())
    return f"{len(cluster.records)}: {labels}"


def write_candidates(
    path: Path, header: dict[str, Any], sections: dict[str, Any]
) -> None:
    """Write candidates.yaml: an explanatory comment, the header, the sections."""
    path.parent.mkdir(parents=True, exist_ok=True)
    comment = (
        "# Candidate vocabulary terms from open extraction (`chatcde vocab extract`).\n"
        "# GENERATED: do not edit by hand. Curate the terms into\n"
        "# vocab/draft_vocabulary.yaml "
        "and record the changes in vocab/CHANGELOG.md (docs/decisions/0019).\n"
        "# n_records counts sampled records (denominator: sample.records_labelled);\n"
        "# all counts come from the catalogue, never from the model.\n\n"
    )
    body = yaml.safe_dump(
        {"generated": header, **sections},
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(comment + body, encoding="utf-8")
    tmp.replace(path)


def propose_terms(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    client: EndpointClient,
    *,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> ProposeSummary:
    """Cluster the sample's labels, ask for proposals, and write candidates.yaml.

    Args:
        con: Open catalogue connection (read-write, for the caches).
        cfg: Configuration (``models``, ``embedding``, ``vocab``, ``vocab_extract``).
        client: Endpoint client.
        force: Ask the model again for clusters that have a cached proposal.
        progress: Called with (done, total) after each proposal.

    Raises:
        RuntimeError: No labels yet (run the label step first).
    """
    s = cfg.vocab_extract
    tagging, label_prompt = cfg.models.tagging, s.extract_prompt
    tables.ensure(
        con, "vocab_sample", "vocab_labels", "vocab_label_state", "vocab_proposals"
    )
    n_records, stats, label_rows = load_label_stats(con, tagging, label_prompt)
    if not stats:
        raise RuntimeError("no labels yet: run `chatcde vocab extract --step label`")
    all_labels = sorted({st.norm for kind_stats in stats.values() for st in kind_stats})
    vectors = embed_labels(
        con,
        client,
        all_labels,
        model=cfg.models.embedding,
        dimensions=cfg.embedding.dimensions,
        batch_size=cfg.embedding.batch_size,
    )
    vocab = load_vocabulary(cfg.vocab.file)
    system_template = load_prompt(cfg.prompts.dir, s.propose_prompt)
    kinds = [k for k in KINDS.values() if k in stats]
    clusters = {
        k: cluster_kind(k, stats[k], vectors, s.cluster_threshold) for k in kinds
    }
    big = {
        k: [c for c in cs if len(c.records) >= s.min_records]
        for k, cs in clusters.items()
    }
    small = {
        k: [c for c in cs if len(c.records) < s.min_records]
        for k, cs in clusters.items()
    }

    cache = {
        key: Proposal.model_validate_json(js)
        for key, js in con.execute(
            "SELECT cluster_key, proposal_json FROM vocab_proposals "
            "WHERE model = ? AND prompt_version = ?",
            [tagging, s.propose_prompt],
        ).fetchall()
    }
    seeds = {k: {d: seed_terms(vocab, d) for d in TARGET_DIMENSIONS[k]} for k in kinds}
    systems = {
        k: fill_prompt(
            system_template,
            {
                "kind": k,
                "kind_description": KIND_DESCRIPTIONS[k],
                "dimensions": ", ".join(TARGET_DIMENSIONS[k] + ["other"]),
            },
        )
        for k in kinds
    }
    jobs = [c for k in kinds for c in big[k] if force or c.key() not in cache]
    cached = sum(len(big[k]) for k in kinds) - len(jobs)

    def ask(cluster: Cluster) -> Proposal:
        return chat_json(
            client,
            model=tagging,
            system=systems[cluster.kind],
            user=_cluster_message(cluster, seeds[cluster.kind], 40),
            schema=Proposal,
            max_tokens=s.max_tokens,
            extra_body=s.extra_body,
        )

    failed = 0
    version, stamp = code_version(), now()
    with ThreadPoolExecutor(max_workers=cfg.llm.max_concurrency) as pool:
        futures = [(c, pool.submit(ask, c)) for c in jobs]
        for done, (cluster, fut) in enumerate(futures, start=1):
            try:
                proposal = fut.result()
            except (LLMReplyError, EndpointError) as exc:
                failed += 1
                log.warning("No proposal for a %s cluster: %s", cluster.kind, exc)
            else:
                key = cluster.key()
                cache[key] = proposal
                con.execute(
                    "DELETE FROM vocab_proposals WHERE cluster_key = ? AND model = ? "
                    "AND prompt_version = ?",
                    [key, tagging, s.propose_prompt],
                )
                tables.insert(
                    con,
                    "vocab_proposals",
                    [
                        tables.ProposalRow(
                            cluster_key=key,
                            kind=cluster.kind,
                            model=tagging,
                            prompt_version=s.propose_prompt,
                            proposal_json=proposal.model_dump_json(),
                            code_version=version,
                            created_at=stamp,
                        )
                    ],
                )
            if progress:
                progress(done, len(jobs))

    sample_meta = con.execute(
        "SELECT any_value(seed), any_value(sample_size), any_value(stratum_floor), "
        "count(*) "
        "FROM vocab_sample"
    ).fetchone()
    strata = dict(
        con.execute(
            "SELECT stratum, count(*) FROM vocab_sample GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    header = {
        "at": stamp.isoformat(),
        "code_version": version,
        "vocab_file": cfg.vocab.file.name,
        "vocab_version": cfg.vocab.version,
        "models": {"tagging": tagging, "embedding": cfg.models.embedding},
        "prompts": {"extract": label_prompt, "propose": s.propose_prompt},
        "clustering": {
            "method": "average linkage, cosine similarity",
            "threshold": s.cluster_threshold,
            "min_records": s.min_records,
            "embedding_dimensions": cfg.embedding.dimensions,
        },
        "sample": {
            "seed": sample_meta[0] if sample_meta else None,
            "target_size": sample_meta[1] if sample_meta else None,
            "stratum_floor": sample_meta[2] if sample_meta else None,
            "records_sampled": sample_meta[3] if sample_meta else 0,
            "records_labelled": n_records,
            "strata": strata,
        },
    }
    sections: dict[str, Any] = {}
    for k in kinds:
        sections[k] = {
            "candidates": [
                _candidate(c, _checked(cache.get(c.key()), k, seeds[k]), cfg)
                for c in big[k]
            ],
            "unclustered": [_unclustered(c) for c in small[k]],
        }
    write_candidates(s.output, header, sections)
    log.info("Wrote %s", s.output)
    return ProposeSummary(
        records=n_records,
        labels={k: label_rows.get(k, 0) for k in kinds},
        distinct={k: len(stats[k]) for k in kinds},
        clusters={k: len(clusters[k]) for k in kinds},
        candidates={k: len(big[k]) for k in kinds},
        unclustered={k: len(small[k]) for k in kinds},
        proposed=len(jobs) - failed,
        cached=cached,
        failed=failed,
        output=str(s.output),
    )
