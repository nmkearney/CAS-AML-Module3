# 0012: Embeddings and the vector index

- Status: accepted
- Date: 2026-11-07

## Context
Issue #21: embed the chunks (decision 0011) with the `embedding` role model
(qwen3-embedding-4b, 2,560 dimensions; alternative granite-embedding-107m-multilingual,
384 dimensions; decision 0003) and choose where the vectors live. The real corpus will
have an estimated ~100k chunks. Bundles (decision 0004) ship the index, and the open
edition is the same index filtered to fewer records. GPUStack handles about 4 parallel
requests per model.

## Decision
- **Store: an `embeddings` table in the catalogue DuckDB**, one row per (chunk, model):
  `chunk_id, record_id, model, dims, fingerprint, vector FLOAT[], code_version,
  embedded_at`. It is a Track B table created in `chatcde.store.embeddings`, not a
  shared `schemas.py` table.
- **Search: exact cosine scan in SQL** (`list_dot_product` on L2-normalised vectors),
  optionally restricted to a list of record IDs (how `Filters` will be applied in #22).
  No approximate index: at ~100k × 1,024 the scan is fast enough, and results are exact
  and reproducible.
- **Dimensions: truncate to `embedding.dimensions` (1,024) and renormalise.**
  Qwen3-Embedding is trained so that its leading dimensions carry most of the meaning
  (Matryoshka), so 1,024 keeps most quality at 40% of the size (~400 MB instead of ~1 GB
  for 100k chunks). Truncation happens in our code, so it works even if the server
  ignores the `dimensions` request parameter. Smaller vectors (granite) are kept whole.
- **Inputs:** documents are embedded as `title + blank line + chunk text`
  (`embedding.include_title`), so chunks from the middle of a paper still carry its
  topic. Queries get the model's instruction from `embedding.query_instructions`
  (Qwen3-Embedding is trained with `Instruct: … / Query: …`); models without one get the
  bare query.
- **Client:** a small `httpx` client (`chatcde.store.endpoint.EndpointClient`) for the
  OpenAI-compatible `/embeddings` route, TLS verified with `certifi`, retries on
  timeouts, 429 and 5xx. B3 adds `/rerank` to it. No `openai` dependency yet.
- **Running:** batches of `embedding.batch_size` (32), up to `llm.max_concurrency` (4) in
  parallel; each batch is committed on return, so an interrupted run resumes. A row's
  fingerprint hashes model, dimensions and input text; changed or deleted chunks lose
  their vectors and new ones are embedded. `--force` re-embeds everything for the model;
  `--alternative` embeds with the alternative model next to the first choice.

## Consequences
- One file holds catalogue and index: `chatcde export` needs no extra step, and the open
  edition is a `DELETE` of non-open records' full-text chunks and their vectors.
- Alternatives considered: DuckDB's `vss` extension (HNSW; needs an extension download,
  persistence still experimental), LanceDB and FAISS (new dependencies and a second
  file to bundle). Revisit if the scan gets slow (> ~1 s per query) or the corpus grows
  well beyond 2,500 records.
- Not yet verified against GPUStack in this branch (no key in the development shell):
  run `chatcde index embed --limit 50` on the VPN and check the dimensions and timing.
- Changing `embedding.dimensions`, `include_title` or the model re-embeds everything;
  changing a query instruction needs no re-embedding.
- `chatcde evaluate questions` (#28) should compare 1,024 against full dimensions, and
  the first-choice model against the alternative.
