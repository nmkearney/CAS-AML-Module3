# 0011: Chunking records and pages for search

- Status: accepted
- Date: 2026-11-07

## Context
Search (issue #22) and the reading tools (#23) work on passages, not whole records. The
reranker (`jina-reranker-v2-base-multilingual`) has a 1K-token context, so passages
must stay well under that with room for the query. The corpus is multilingual (EN, DE,
some FR/ES), many PDFs start with front matter, some old PDFs have noisy OCR text, and
about half the records have no full text at all. The `Chunk` model in `schemas.py` has
no provenance field, and the rule is that every pipeline output records what produced it.

## Decision
- **Size:** at most `retrieval.chunk_tokens` (500) estimated tokens per chunk, with
  `retrieval.chunk_overlap_tokens` (50) repeated from the previous chunk.
- **Token estimate instead of a tokenizer:** tokens = ceil(word-like pieces ×
  `retrieval.tokens_per_word`), where a piece is a run of letters/digits or one
  punctuation mark, and the factor is 1.4. This avoids downloading a tokenizer (not
  possible offline, and not provided by GPUStack's OpenAI-compatible API) and errs on
  the large side for English and German on XLM-R-style tokenizers. On the fixtures the
  mean chunk is ~430 estimated tokens.
- **Splitting:** text is cleaned (NFKC, soft hyphens removed, hyphenated line breaks
  rejoined before lower-case letters, whitespace collapsed), split into paragraphs and
  sentences, and the sentences are packed greedily into chunks. Over-long sentences are
  split on spaces. Each sentence keeps its page, so `page_start`/`page_end` are exact even
  when page numbers have gaps (the fixtures have pages 1, 2, 3, 5, 6, 7).
- **Which texts:** abstract chunks (source `abstract_boris` or `abstract_openalex`) if
  the record has an abstract; full-text chunks (`fulltext`) if it has pages; otherwise
  one `metadata` chunk of title, keywords and container. Every record is findable.
  Front matter is kept; the reranker pushes it down.
- **Text is not prefixed with the title**, so `read_section` can quote chunks verbatim.
  If retrieval needs the title, it is added at embedding time (#21).
- **IDs:** `{record_id}:{n}`, abstract or metadata chunks first, then full text.
- **Idempotency and provenance:** a Track B table `chunk_state` (record_id, fingerprint,
  n_chunks, chunker_version, code_version, settings, chunked_at) sits next to `chunks`.
  The fingerprint hashes the record's text fields, its pages, the settings and
  `CHUNKER_VERSION`, but not the code version, so an unrelated commit does not force a
  full re-chunk. A run redoes only new or changed records (or all with `--force`), each
  in its own transaction, and removes chunks of records no longer in `records`.

## Consequences
- Chunking needs no network and no model; it takes about a second for the fixtures.
- Changing the chunking logic requires bumping `CHUNKER_VERSION` in
  `src/chatcde/store/chunking.py`, otherwise unchanged records keep their old chunks.
- Revisit: measure real token counts once the embedding/reranker tokenizer is
  reachable and tune `tokens_per_word`; detect section headings to fill `Chunk.section`;
  consider adding provenance to `Chunk` via a `schemas` issue instead of the side table;
  consider dropping near-empty front-matter chunks if they crowd search results.
