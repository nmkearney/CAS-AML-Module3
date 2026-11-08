# 0020: Search and reading tools

- Status: accepted
- Date: 2026-11-08

## Context
Issue #23 asks for the first three tools: `search_publications`, `get_publication` and
`read_section`. Their signatures in `chatcde/tools/core.py` are the contract shared
between the tracks and are what the agent sees. They take only what an LLM should
choose (a query, filters, IDs, pages), not a config or a database connection. The
hybrid search engine is done (0013). Chunks keep their text verbatim, without a title
prefix, so that `read_section` can quote them (0011). `Record` now has both BORIS's
DataCite DOI and the publisher's DOI (#38, 0010).

## Decision
- **Tool context.** `chatcde.tools.context.ToolContext` holds the config and an endpoint
  client that is created on first use. The CLI and the agent activate one with
  `use_context(...)`. Without one, the first call builds a default context from
  `load_config()`. Each call opens the catalogue read-only and closes it again, so the
  tools never block a pipeline stage that writes, and they can run in threads. The
  implementations in `chatcde.tools.publications` take the context explicitly, so they
  are easy to test. `core.py` passes them the active context; only its bodies and
  docstrings changed, not its signatures.
- **`ToolError`** covers problems the caller can fix: an unknown ID, an empty query,
  `k < 1`, a page range that is wrong or too wide, no full text, no index, tag filters
  without tags, or the endpoint failing. Each message says what to do instead, for
  example "use search_publications", "its full text covers pages 1–3", or "its abstract
  is available through get_publication". The agent (#25) returns the message to the
  model as the tool result. The CLI prints it and exits with status 1.
- **`search_publications`** wraps `hybrid_search` with the config's settings. It returns
  `k` passages, capped at `tools.max_k` (20), with at least `max(retrieval.top_k, k)`
  candidates before reranking. Each passage keeps the reranker's score and gets a
  `Citation` whose `page` is the chunk's first page.
- **The DOI in a citation is the publisher's DOI when there is one, otherwise BORIS's
  own**, because readers cite the publisher's DOI. Both DOIs stay in `get_publication`.
- **`get_publication` accepts** a BORIS UUID, either DOI (bare, `doi:` or a doi.org
  URL, in any case) or a BORIS handle (or a URL ending in one). If a DOI matches
  several records, the first by ID is used and a warning is logged. Authors come in
  BORIS order, tags by dimension and then by confidence, highest first. The download
  entry is the latest one. A missing `authors`, `tags` or `downloads` table gives empty
  values rather than an error.
- **`read_section` returns the `fulltext` chunks that overlap the page range**, verbatim
  and in reading order. It does not return the raw pages: the chunks are what search
  cited, they exist in both editions, and they have clean text. Neighbouring chunks share
  `retrieval.chunk_overlap_tokens` (50) of text, as the docstring tells the model. A
  call covers at most `tools.max_section_pages` (10) pages, to protect the agent's
  context.
- **CLI:** `chatcde search` (with every `Filters` field as an option), plus two new
  commands for the other tools: `chatcde show` (`get_publication`) and `chatcde read`
  (`read_section`). All three accept `--json`, which prints exactly what the agent sees.

## Consequences
- The agent loop (#25) can generate tool definitions straight from `core.py` and call
  the functions inside one `use_context` block per conversation.
- Track A's `coverage_report` (#11, PR #45) calls `load_config()` directly. It could use
  `get_context()` instead, so that tests and the agent can point it at another
  catalogue. This is a small follow-up once both are merged.
- Not yet verified against GPUStack: `chatcde search` in hybrid mode with reranking on
  the VPN. The offline tests use a fake endpoint.
- Revisit:
  - group passages by publication, if the agent wastes calls on several chunks of one
    paper;
  - return raw pages from `read_section` if chunk overlap confuses quoting;
  - add a `section` argument once `Chunk.section` is filled (0011);
  - match DOIs in the OpenAlex links (0010).
