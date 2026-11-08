# ChatCDE

A local research assistant over the publications of the Centre for Development and
Environment (CDE), University of Bern. It indexes CDE's records from BORIS Portal and
lets researchers ask a local LLM about gaps, frontiers and more, with every answer
traceable to BORIS IDs. **Prototype**; see `docs/planning.md` for the design and
`CONTRIBUTING.md` for the workflow.

## Install

Python 3.12 or newer. Install from a clone, in editable mode (the default config is
read from `config/default.yaml` in the clone):

```bash
git clone https://github.com/nmkearney/CAS-AML-Module3.git ChatCDE
cd ChatCDE
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
chatcde --help
```

Most commands are placeholders for now; each one names the issue that implements it.

## Harvest

On the UniBe network or VPN (the BORIS REST API is closed to other traffic):

```bash
chatcde harvest metadata            # BORIS search -> data/catalogue.duckdb (records, authors)
chatcde harvest metadata --offline  # rebuild the tables from the saved pages, no network
chatcde harvest metadata --force    # fetch every page again
```

The fetch saves every search page under `data/raw/boris/` and resumes if it is
interrupted. It takes about 2.5 minutes for the ~87 pages, with 0.3 s between requests.
Off the VPN it stops with exit code 2. Which records count as CDE's is explained in
`docs/decisions/0008-harvest-metadata.md`. The counts of each run are in
`data/raw/boris/harvest_summary.json`.

Then the files, also on the VPN:

```bash
chatcde harvest files               # main PDF per record -> data/raw/files/<record_id>.pdf
chatcde harvest files --limit 20    # trial run on 20 records
chatcde harvest files --force       # try every record again
```

Every record gets one row in the `downloads` table: `downloaded` (with sha256 and
size), `restricted` (BORIS answered 401/403), `no_file` (no PDF in the `ORIGINAL`
bundle) or `error`. A rerun skips finished records and retries errors. The command
prints the counts by status and by BORIS access × status; they are also saved to
`data/raw/boris/files_summary.json`. A full run needs about 5 GB and takes under an
hour. See `docs/decisions/0009-download-files.md`.

Then the OpenAlex enrichment (public API, no VPN needed):

```bash
chatcde harvest openalex            # abstracts + OA links for every record's DOIs
chatcde harvest openalex --offline  # re-apply the saved lookups (e.g. after a config change)
chatcde harvest openalex --force    # look every DOI up again
```

Each record's publisher DOI and BORIS DOI are looked up, 100 per request (about
30 requests). An OpenAlex abstract is written only where BORIS has none
(`abstract_source = abstract_openalex`). Open-access links go to
`data/raw/openalex/oa_links.csv`, and the counts to `openalex_summary.json` in the
same folder. No key is needed. An optional free key is read from
`CHATCDE_OPENALEX_API_KEY`. See `docs/decisions/0010-openalex-enrichment.md`.

## Index

Split the catalogue's texts into passages for search (no network needed):

```bash
chatcde index chunk                 # records + pages -> chunks table
chatcde index chunk --limit 20      # trial run on 20 records
chatcde index chunk --force         # chunk every record again
```

Chunks hold at most `retrieval.chunk_tokens` estimated tokens (500), with
`chunk_overlap_tokens` (50) repeated from the previous chunk. A record gets abstract
chunks if it has an abstract, full-text chunks if it has pages, and one chunk of title,
keywords and container if it has neither, so every record can be found. A rerun only
chunks records whose text or chunk settings changed; the `chunk_state` table records
each record's fingerprint, code version and settings. See
`docs/decisions/0011-chunking.md`.

Then embed the chunks with the `embedding` model on the local endpoint (needs the key
in `CHATCDE_LLM_API_KEY`):

```bash
chatcde index embed                 # chunks -> embeddings table (vector index)
chatcde index embed --limit 50      # trial run on 50 chunks
chatcde index embed --alternative   # also embed with the alternative model, to compare
chatcde index embed --force         # embed every chunk again
```

Each chunk is embedded as title + text, truncated to `embedding.dimensions` (1,024)
and normalised. Requests go out in batches of 32, up to `llm.max_concurrency` at a
time. A rerun embeds only new or changed chunks. Vectors stay in the catalogue file, so
bundles carry them. See `docs/decisions/0012-embeddings.md`.

Build the keyword (BM25) index, which runs locally with no endpoint:

```bash
chatcde index bm25                  # chunks -> bm25_docs + bm25_postings tables
chatcde index bm25 --force          # index every chunk again
```

Then try the hybrid search. BM25 and vector search each return `retrieval.top_k` (40)
candidates. The two lists are fused by reciprocal rank, and the reranker model reorders
the fused list. The command prints each stage's rank and score:

```bash
chatcde index search "Bodenerosion in Bergregionen"
chatcde index search "land acquisitions Laos" --year-from 2010 --lang en
chatcde index search "living lab" --mode bm25 --no-rerank   # offline, keywords only
```

`--mode` is `hybrid`, `bm25` or `vector`. Filters: `--year-from`, `--year-to`, `--unit`,
`--type`, `--lang`, `--country`. This command is for checking the search. The
user-facing search with citations is `chatcde search` (issue #23). See
`docs/decisions/0013-hybrid-search.md`.

## Vocabulary drafting

Draft vocabulary terms from CDE's own publications (needs the key in
`CHATCDE_LLM_API_KEY`). The command runs three steps, each resumable:

```bash
chatcde vocab extract                         # all three steps in order
chatcde vocab extract --step sample           # draw the stratified sample (no network)
chatcde vocab extract --step label --limit 10 # trial: label 10 sampled records
chatcde vocab extract --step label            # label the rest
chatcde vocab extract --step propose          # cluster labels -> vocab/candidates.yaml
```

- **sample:** about 250 records with an abstract or page text, stratified by CDE unit ×
  decade × language. Every stratum gets at least `vocab_extract.stratum_floor` records,
  and the rest are shared out in proportion. The draw is seeded and stored in
  `vocab_sample`, with a copy in `data/vocab/sample.csv`.
- **label:** the `tagging` model writes short English labels for methods, themes, data
  types, settings, systems and actors. Each label comes with a quote in the original
  language, and the code checks that the quote occurs in the text. Results go to
  `vocab_labels`, and records already labelled are skipped.
- **propose:** labels are embedded and clustered (average linkage, cosine
  ≥ `cluster_threshold`). The `tagging` model names and defines each cluster and
  compares it with the draft terms. The result is `vocab/candidates.yaml`, with counts
  taken from the catalogue.

`--force` redoes the chosen step. The candidates are curated by hand into
`vocab/draft_vocabulary.yaml`. See `docs/decisions/0019-open-extraction.md`.

## Configuration

Settings are merged from these files, later ones winning:

1. `config/default.yaml`: committed defaults; read it for what can be set.
2. `~/.config/chatcde/config.yaml` (or `$XDG_CONFIG_HOME/chatcde/config.yaml`), if present:
   your personal settings.
3. A file given with `chatcde --config PATH …` or in `$CHATCDE_CONFIG`.

An override only needs the keys it changes:

```yaml
# ~/.config/chatcde/config.yaml
llm:
  base_url: "https://gpustack.unibe.ch/v1"
models:
  agent: "qwen3.8-27b"
```

Unknown keys are errors, so typos are caught. Relative paths are resolved against the
clone, not the current folder. The LLM API key is never put in a file: set the
environment variable named by `llm.api_key_env` (default `CHATCDE_LLM_API_KEY`).

```bash
chatcde config path   # which files are merged
chatcde config show   # the merged result
```

To check that the endpoint and every configured model (`models` and
`model_alternatives`) work, i.e. tool calling, JSON-schema output, embeddings,
reranking and concurrency, run `python3 scripts/check_gpustack.py`. It prints a table
and writes a JSON report to `data/checks/`; `--help` lists the options.

## Logging

Messages go to stderr; results go to stdout, so they can be piped. `-v` shows debug
messages and `-q` only warnings. A full log is kept in `data/logs/chatcde.log`
(set `logging.file: null` to turn it off).

## Development

```bash
ruff check . && ruff format --check .
pytest
```
