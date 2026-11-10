# 0032: Data bundles: a filtered copy of the catalogue, packed as one tar.gz

- Status: accepted
- Date: 2026-11-10

## Context
Decision 0004 sets out two editions built from one index: **full** (every full
text) and **open** (full text only for open-access records). Issue #17 builds
them with `chatcde export`, and #18 downloads them with `chatcde fetch-data`.

The whole local index lives in one DuckDB file, `data/catalogue.duckdb`:
- Track A's records, authors, tags, pages and downloads
- Track B's chunks, embeddings and BM25 tables

BM25 computes N, avgdl and df at query time, so removing rows needs no
re-indexing. The readers (`coverage.local_edition`, `answer.data_note`) take the
edition from `manifest.json` in `data.dir`.

Every parsed full text comes from a record with `access = open`: all 1,542 of
them, because restricted files return 401 even on the VPN (decision 0009). So
today the two editions hold the same content.

## Decision
- **Contents.** A bundle is `chatcde-<edition>-<version>.tar.gz` holding three
  files, flat:
  - `catalogue.duckdb`: the tables in `BUNDLE_TABLES` (`chatcde.bundle.export`).
    These are records, authors, tags, downloads, pages, chunks, embeddings,
    bm25_docs, bm25_postings, and the provenance tables tag_state, parse_state
    and chunk_state.
  - `vocabulary.yaml`: a copy of `vocab.file`.
  - `manifest.json`: a `BundleManifest` with the sha256 of the other two files.
  - Pipeline working tables (tag_runs, tag_pilot, vocab_*) and the PDFs stay out.
- **Open filter:**
  - pages and parse_state keep only records with `access = 'open'`.
  - chunk_state drops only the records whose full-text chunks were dropped, so a
    later `chatcde index chunk` (after `--top-up`) redoes exactly those.
  - chunks keep abstract and metadata chunks for every record, and full-text
    chunks only for open records.
  - embeddings and the BM25 tables keep the rows whose chunk survives.
  - Records, authors, tags and downloads are copied whole.
  - A check after the build fails the export if a non-open record has pages or
    full-text chunks, or if any vector or posting has no chunk.
- **Next to the archive:** a copy of the manifest
  (`<name>.manifest.json`), so `fetch-data` can read the version and checksums
  before downloading, and `<name>.tar.gz.sha256`.
- **Version:** today's date (`YYYY.MM.DD`) unless `--version` is given. The
  output folder is `data.bundles_dir`.
- **Build safety:**
  - The source catalogue is attached read-only.
  - Each table is created from the source's own DDL.
  - The archive is written under a temp name and renamed.
  - An existing archive is skipped unless `--force`.

## Consequences
- One file to host per edition. Unpacking into `data.dir` puts `manifest.json`
  where the readers look for it.
- The open filter is a guarantee for later data: `--top-up`, OA copies found via
  OpenAlex (decision 0010), or a change in what BORIS serves. Today it removes
  nothing.
- Each bundle is built by one DuckDB version. A much older DuckDB may not read
  the file. `fetch-data` should say which version built it if opening fails.
- Revisit:
  - **Licences.** "Open" in BORIS means the file can be downloaded. Some open
    PDFs may still carry publisher terms that do not allow redistribution.
    Check them before the open edition is published anywhere public.
  - **Size.** gzip does little for float vectors. If the bundle grows, store
    the vectors as float16, or drop the full-size page text from the open
    edition.
