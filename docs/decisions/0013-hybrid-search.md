# 0013: Hybrid search, fusion and reranking

- Status: accepted
- Date: 2026-11-07

## Context
Issue #22: search the chunks (0011) with BM25 and the vectors (0012), merge the two
result lists, rerank them with the `reranker` role model (jina-reranker-v2-base-multilingual,
1K-token context; 0003) and apply the shared `Filters`. This is the engine behind
`search_publications` (#23). The corpus is multilingual (mostly EN and DE), and the
catalogue file is what the bundles ship (0004).

## Decision
- **BM25 lives in two Track B tables in the catalogue**: `bm25_docs` (chunk length,
  fingerprint, code version) and `bm25_postings` (term, chunk, term frequency). They are
  built by `chatcde index bm25` (`chatcde.store.bm25`). Okapi BM25 is scored in one SQL
  query, with `k1 = 1.2`, `b = 0.75` and IDF `ln(1 + (N − df + 0.5)/(df + 0.5))`.
  N, df and the mean length are computed over the whole index, so a filter changes which
  chunks are returned but not their scores. The indexed text is `title + chunk text`,
  the same text the embedding model sees.
- **Tokens: case-fold, strip accents, split on `\w+`, drop a short EN/DE/FR/ES stopword
  list and single Latin letters. No stemming.** BM25 is there for exact terms such as
  places, acronyms, project and method names. Inflection and cross-language matches are
  left to the vectors.
- **Incremental:** each chunk's fingerprint covers its text and `TOKENIZER_VERSION`.
  A rerun indexes only new or changed chunks and drops chunks that are gone. `--force`
  rebuilds the whole index.
- **Fusion: reciprocal rank fusion** with `retrieval.rrf_k = 60`. Each retriever returns
  `retrieval.top_k` (40) candidates, and the fused list is cut to 40. Ranks are fused
  rather than scores, because BM25 and cosine scores are on different scales. Ties are
  broken by chunk ID so that results are reproducible.
- **Reranking:** one `/v1/rerank` request (Jina/Cohere format) per search, with the 40
  fused candidates as `title + chunk text`, each cut to `retrieval.rerank_max_chars`
  (2,000 characters, about 500 tokens) so that query and passage fit the reranker's 1K
  context. The best `retrieval.top_n` (8) are returned.
- **Every result keeps its trail:** BM25 rank and score, vector rank and score, RRF score
  and rerank score (`chatcde.store.search.RankedChunk`). `chatcde evaluate questions` (#28)
  can then compare the stages.
- **Filters → record IDs** (`chatcde.store.filters.filter_record_ids`), which both
  retrievers apply. Fields combine with AND, and the values within one field with OR.
  Matching ignores case. Years are inclusive. Tag fields (countries, regions, sub_areas,
  methods, sdgs) need `confidence >= min_confidence`. A tag filter on a catalogue without
  tags is an error, not an empty result. B5 reuses the same function for counts.
- **Fallbacks:** in `hybrid` mode, a missing index is replaced by the other retriever,
  with a warning. A failing reranker is an error: `--no-rerank` / `rerank=False` is the
  explicit way to skip it, so a degraded ranking is never returned silently.
- **CLI:** `chatcde index search` is a debugging command that prints every stage. The
  user-facing `chatcde search` with citations comes with #23.

## Consequences
- No new dependency and no DuckDB extension download. The index stays in the one
  catalogue file, and the open edition is still a `DELETE` of rows (0012).
- On the fixtures (292 chunks), the BM25 build takes 0.5 s, with about 34k postings.
  The real corpus should have about 100k chunks and roughly 10M postings. A query joins
  only the postings of its own terms; if this gets slow (more than about 0.5 s), sort the
  postings by term or add an index on `term`.
- Not yet verified against GPUStack in this branch: on the VPN, run `chatcde index search`
  in hybrid mode with reranking and check that the rerank scores and the timing are
  sensible (about 1–2 s per query).
- Revisit with #28: stemming or language-specific analysers, separate depths per
  retriever, weighted RRF, whether 40 candidates is enough for the reranker, and whether
  front-matter chunks need to be pushed down before reranking.
