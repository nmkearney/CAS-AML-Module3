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

## Parse

Turn the downloaded PDFs into page text (no VPN needed; OCR needs the key in
`CHATCDE_LLM_API_KEY`):

```bash
chatcde parse --no-ocr              # text layer of every PDF; scanned pages stay pending
chatcde parse                       # the same, then OCR of pending pages with models.vision
chatcde parse --limit 20            # trial run on 20 PDFs
chatcde parse --record <record_id>  # one record (repeatable)
chatcde parse --force               # read every PDF again (cached OCR answers are reused)
```

Every page of each PDF becomes one row in the `pages` table, with its text, its
detected language (`en`, `de`, `fr`, … or empty) and whether it came from OCR. Pages
with almost no text but covered by images (scans), and pages whose text layer is
garbled, are rendered and transcribed by the vision model, 4 at a time. Answers are
cached in `data/text/ocr/`, so a rerun never asks twice for the same page. The
`parse_state` table keeps each record's status (`done`, `needs_ocr`, `error`); a rerun
skips finished records and retries the rest. Counts go to
`data/text/parse_summary.json`. Thresholds are in the `parse` section of the config.
See `docs/decisions/0014-parsing.md`.

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
user-facing search with citations is `chatcde search` (below). See
`docs/decisions/0013-hybrid-search.md`.

## Coverage

See how much of the catalogue the assistant can actually read before trusting that a
topic is rare:

```bash
chatcde coverage                          # totals, then by year, type, language, access
chatcde coverage --by access --by type    # only these breakdowns
chatcde coverage --year-from 2015 --unit "Sustainable Land Systems"
chatcde coverage --json                   # the CoverageReport the agent's tool returns
```

Each record counts once, as **full text** (at least one parsed page with text),
**abstract only** or **metadata only**. Until `chatcde parse` has run, a downloaded PDF
stands in for full text, and the command says so. Each breakdown shows the three shares.
A record counts under its first listed language. The heading names the edition: `open`
when the data came from an open bundle, otherwise `full`. Filters: `--year-from`,
`--year-to`, `--unit`, `--type`, `--lang`. See `docs/decisions/0015-coverage.md`.
## Search and read

These commands call the same tools the assistant uses (`search_publications`,
`get_publication` and `read_section`) and print what they return. Add `--json` to get
the exact result the agent sees.

```bash
chatcde search "large-scale land acquisitions in Laos" -k 5
chatcde search "Weidewirtschaft" --year-from 2015 --country KEN --sdg 15 --full
chatcde show 10.1659/mrd.4204           # BORIS ID, DOI (publisher or BORIS) or handle
chatcde read 2760e535-c973-4524-ab84-3a1c71b9bf3a 2 3   # pages 2-3, verbatim
```

`search` returns passages, best first. Each passage has a score, its pages, its source
(full text, abstract or metadata), the BORIS ID and the DOI. The DOI is the publisher's
when there is one, otherwise BORIS's own. Like `index search`, it needs the endpoint
for the query embedding and the reranker. `show` and `read` run locally.

`read` returns the full-text chunks that overlap the pages, so neighbouring passages
share a few sentences. It reads at most `tools.max_section_pages` (10) pages per call,
and `search` returns at most `tools.max_k` (20) passages. Records without parsed full
text have no pages; `show` gives their abstract. See
`docs/decisions/0020-search-and-reading-tools.md`.

## Count and find gaps

These commands count tags in the catalogue and need no model. They call the tools
`list_vocabulary`, `count_by` and `find_gaps`.

```bash
chatcde schema                                  # the 17 dimensions: terms, records tagged
chatcde vocab -d method                         # one dimension's terms with record counts
chatcde count sub_area                          # records per sub-area
chatcde count impact_area method --year-from 2015 --lang de
chatcde count sub_area method --gaps            # combinations far rarer than expected
chatcde count region sub_area --gaps --max-ratio 0.5 --all-types --json
```

Counts are of records, not tags: a record with two methods counts under both. Only tags
with confidence of at least `--min-confidence` (0.5) count. Every result states its
denominator: the number of matching records and how many of them have no tag. With two
dimensions, the matrix counts only records tagged on both.

`--gaps` compares each combination with the count expected if the two dimensions were
independent (row total × column total / records tagged on both). It lists those with at
most `--max-ratio` × expected. Combinations expected to hold fewer than
`gaps.min_expected` (5) records are not judged. Conference items are left out, as set
by `gaps.exclude_types` (decision 0018), unless you pass `--type` or `--all-types`. The
output says how many were left out. See `docs/decisions/0021-counts-and-gaps.md`.

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

## Ask the assistant

The local agent model (`models.agent`) answers questions by calling the tools above:
it searches, reads and counts, then answers with numbered sources. It needs the key in
`CHATCDE_LLM_API_KEY`.

```bash
chatcde ask "What has CDE found on large-scale land acquisitions in Laos?"
chatcde ask "Welche Methoden nutzt CDE in der Landgovernance-Forschung?" --log data/logs/answers.jsonl
chatcde ask "Which methods are rare in SL research?" --json --hide-tools > answer.json
chatcde chat                                    # follow-up questions; /reset, /exit
```

Each tool call is shown as it runs (on stderr; `--hide-tools` turns this off). The
answer is followed by:
- its **sources**: number, title, year, cited pages, DOI and BORIS links;
- **warnings** for anything unsupported: a record no tool returned, an ID not in the
  catalogue, a used-up tool budget, or a cut-off answer;
- a **data note**: which edition answered, and how many publications have full text,
  only an abstract, or only metadata.

`--json` prints the full result, including every tool call and the model, prompt,
vocabulary and code versions. `--log FILE` appends that result as a JSON line.
`--alternative` uses `model_alternatives.agent`.

How the agent works: the tool definitions are generated from the tool functions' type
hints and docstrings (`chatcde.tools.specs`). The model may make at most
`agent.max_tool_calls` (12) calls per question; then it has to answer. It cites records
as `[BORIS:<record_id>]` or `[BORIS:<record_id>, p. 4]`, and each cited ID is checked
against the catalogue and the conversation's tool results. The system prompt is
`src/chatcde/prompts/agent_system.v1.md`. From Python:

```python
from chatcde.agent import Agent
from chatcde.config import load_config

with Agent(load_config()) as agent:
    result = agent.ask("What has CDE found on large-scale land acquisitions in Laos?")
print(result.answer, [c.citation.record_id for c in result.citations])
```

See `docs/decisions/0022-agent-loop.md` and `docs/decisions/0023-ask-and-chat.md`.

## Find gaps with the assistant

`chatcde gaps` runs the gap analysis as a fixed chain, so every run takes the same
steps:

```bash
chatcde gaps                                         # sub_area x method, all of CDE
chatcde gaps --dims method,region --unit "Sustainable Land Systems"
chatcde gaps --dims sub_area,method --year-from 2015 --max-cells 5 --json > gaps.json
chatcde gaps --no-llm                                # counts and nearby records only
```

1. Code counts the two dimensions within the scope and finds the thin cells, as
   `chatcde count ROW COL --gaps` does.
2. For each of the first `gaps.max_cells` (8) cells, code searches the scope for the
   two terms and lists the records found with their own tags.
3. Code lists the caveats: untagged records, conference items left out, cells not
   shown, and the data edition.
4. The agent model judges each cell in one request with a JSON schema: a real gap, a
   tagging artefact (the work exists but is tagged otherwise), a deliberate choice
   (outside CDE's scope) or unclear, with a reason and the records behind it.

Every number in the table comes from step 1; the model only judges. Record IDs it
cites outside the cell's evidence are dropped and shown in red. The prompt is
`src/chatcde/prompts/gaps.v1.md`, and the settings are in the `gaps` section of the
config. `--alternative`, `--log FILE` and `--json` work as for `ask`. See
`docs/decisions/0024-gaps-workflow.md`.

## Evaluate the assistant

`chatcde evaluate questions` runs the test questions in `tests/questions.yaml` through
the agent and scores the answers with code, not with another model:

```bash
chatcde evaluate questions                      # both agent models, every question
chatcde evaluate questions --models main --kind count --id q03
chatcde evaluate questions --runs 3 --out data/eval/run1.jsonl   # resumes if interrupted
chatcde evaluate questions --rescore data/eval/run1.jsonl        # after editing expectations
```

Each question lists what a good answer contains: records it must cite, tools it must
call, words and numbers it must mention. Every answer must also finish, cite only
catalogue records, and cite only records a tool returned. The command prints a table of
answers and one of models: pass rate, score, record recall, tool calls, time and
tokens. It exits 1 if a model's pass rate is below
`evaluation.min_question_pass_rate` (0.8). Answers are saved as JSON lines in
`evaluation.results_dir`. The question format is in
`docs/decisions/0025-evaluate-questions.md`.
```

See `docs/decisions/0022-agent-loop.md` and `docs/decisions/0023-ask-and-chat.md`.

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
