# ChatCDE: a local research assistant over CDE's publications (prototype)

## What this project is

A prototype Python package, `chatcde`, used from the command line. It indexes
the publications of the Centre for Development and Environment (CDE), University
of Bern, from BORIS Portal and lets researchers ask a local LLM about:

1. **Gaps**: themes, methods, regions or actors CDE has rarely covered.
2. **Frontiers**: directions that build on CDE's strengths, compared with the wider field.
3. Other uses: literature synthesis, cross-country lessons, expertise finder,
   proposal support, onboarding, SDG reporting.

It is a prototype for a demonstration. There are no staff workshops: the developer
makes and records the design choices, so a later phase can revisit them.

Details live in:
- `docs/planning.md`: architecture, interface, each tool's inputs and outputs,
  use cases with tool chains, workflows, build steps. Read it before designing or
  changing any tool or command.
- `docs/schema_draft.md`: the 17-dimension schema with scope notes and the
  design decisions still to record. `vocab/draft_vocabulary.yaml` is its
  machine-readable twin; keep them in sync.
- `docs/decisions/`: one short markdown file per design decision (context,
  decision, consequences, what to revisit later). Add one whenever a choice
  isn't obvious from the code.

These repo files are the reference for the build. The original planning doc
(https://claude.ai/artifact/93ZrZB6GgHHQDUUsyCtKvi) is for discussion and is
re-exported here when it changes in substance.

## Team and tracks

Two developers work in parallel; see `CONTRIBUTING.md` for the full workflow.
- **Track A (Data), Norman Kearney:** `harvest/`, `parse/`, `tag/`, `vocab/`,
  `bundle/` (`chatcde export` / `fetch-data` / `info`). Writes the `records`,
  `authors`, `tags`, `pages` and `downloads` tables.
- **Track B (Assistant), Simon Bader:** `store/`, `tools/`, `agent.py`,
  `workflows/`, `prompts/`, query commands. Writes `chunks` and the search index.
- **Shared contract:** `src/chatcde/schemas.py` and the tool signatures in
  `src/chatcde/tools/core.py` (drafts, to be agreed on day 1). Do not change them
  as a side effect; propose changes in an issue labelled `schemas`.
- At the start of a session the developer says which track they work on.
  Stay within that track's folders; suggest an issue for anything outside them.
- Work on a branch `a/<issue>-name` or `b/<issue>-name`, never on `main`;
  PR descriptions say `Closes #<issue>`.
- Build and test Track B against `tests/fixtures/` until Track A's real catalogue exists.

## Hard rules

- **Python only**, packaged with `pyproject.toml` (src layout); the console
  script is `chatcde`.
- **Everything is reviewable, documented code.** Every step is a CLI command or
  a function with type hints and a docstring explaining what it does and why.
  No logic lives only in notebooks; notebooks are for exploring only.
  Update the README and docs when behaviour changes.
- **Every LLM call goes to a local, OpenAI-compatible endpoint** (GPUStack,
  vLLM or Ollama), set in `config.yaml`. Never call a cloud LLM API from the
  code. Use structured JSON output (a JSON schema) for tagging.
- **No hard-coded settings.** Endpoints, model names, paths, vocabulary version
  and thresholds go in `config/default.yaml`, which users override locally.
- **Reproducibility.** Every pipeline output records the code version, vocabulary
  version, prompt version and model name that produced it. Prompts live as
  versioned files in `src/chatcde/prompts/`.
- **Pipeline stages are resumable and idempotent**, and log what they did.
  Re-running a stage skips finished work unless `--force` is given.
- **Counts come from code, never from the LLM.** Any claim that something is
  rare or missing must come from a catalogue query with its denominator.
  Every answer is traceable to BORIS IDs or DOIs.
- **Be gentle with BORIS:** about 0.3 s between requests, no parallel hammering. Read-only.
- **Never disable TLS verification.** Use `certifi` (python.org builds on macOS lack root certificates).
- **Data stays out of git.** `data/` is gitignored; never commit PDFs, extracted text or indices.
  The one exception is `tests/fixtures/`: up to 6 pages of text each from 30 openly
  licensed records, credited in its README (decision 0007).
- The publications are public, so there are no privacy limits on reading them
  during development. Licences still apply: see the two data editions below.

## Data source facts (verified 2026-10-27)

- BORIS Portal = DSpace 7.6.1 + DSpace-CRIS.
  - REST API: `https://boris-portal.unibe.ch/server/api`. **This only works on the
    UniBe network or VPN.** Off-network it returns an HTML "Temporary Access
    Restriction" page with HTTP 200, so always check that the response is JSON.
  - OAI-PMH: `https://boris-portal.unibe.ch/server/oai/request` (public, but sets
    follow collections, not org units, so don't use it for CDE filtering).
  - Search: `/discover/search/objects?query="Centre for Development and Environment"&dsoType=ITEM`,
    then keep records whose affiliation/credit fields contain the CDE name.
  - Files: `/core/items/{uuid}/bundles` → bundle `ORIGINAL` → bitstreams.
- Field names are partly guessed (`dc.title`, `dc.date.issued`, `dc.type`,
  `dc.language.iso`, `dc.identifier.doi`, `dc.description.abstract`, `dc.subject*`,
  `dc.relation.ispartof`). **Check `data/cde_fields.txt`** (written by the
  inventory script) and fix the code accordingly.
- CDE unit strings look like
  `Centre for Development and Environment (CDE) - Sustainable Land Systems`.

## Inventory results (first run, 2026-10-27)

- 2,659 CDE records; **2,507 publications**; 15 datasets; 21 projects; 56 audiovisual.
- Types: 826 article, 564 conference_item, 305 report, 278 book_section,
  186 working_paper, 134 book, 110 other, 83 magazine_article.
- About 1,155 records have an abstract and about 1,794 have a DOI. Use OpenAlex to fill
  missing abstracts.
- **~70% of a 60-record sample have a file attached** (±12 points). Whether the
  files can actually be downloaded is not yet confirmed.
- Language: en 1,935, de 432, es 55, fr 54; "me" (44) and "an" (2) are likely
  miscoded. The pipeline must be multilingual (at least EN + DE).
- **Only about 600 publications carry a CDE unit** (232 Just Economies & Human Well Being,
  214 Sustainable Governance, 188 Sustainable Land Systems, 81 Transformative
  Education & Science, 45 Services).
- Years: most output 2012–2025; about 270 before 2010; about 250 from 2025 on.
- Known bug in the first run: access rights were merged with `dc.date.accessioned`.
  This is fixed in v3 of `scripts/boris_cde_inventory.py`; re-run it for clean counts.

## Harvest results (`chatcde harvest metadata`, 2026-11-06)

- **3,003 publications** in `data/catalogue.duckdb`: 2,529 name CDE explicitly,
  273 NCCR North-South and 201 legacy records confirmed by a CDE author
  (decision 0008); 50 unconfirmed legacy records are left out.
- 2,111 with a DOI, 1,338 with an abstract, 549 with a CDE unit; 14,298 author rows.
- Access: 1,573 open, 899 metadata only, 531 restricted.
- `cris.virtual.orcid` is not aligned with the authors by position; match it via
  `cris.virtualsource.orcid` (fixed in `chatcde.harvest.mapping`).

## File download results (`chatcde harvest files`, 2026-11-06)

- Main PDF per record in `data/raw/files/<record_id>.pdf`; manifest in `downloads`
  (decision 0009). **1,542 downloaded** (6.3 GB), 913 no_file, 548 restricted, 0 errors.
- **Files marked restricted cannot be downloaded on the VPN**: 0 of 530, all HTTP 401.
  18 records marked open also refuse their file. Full text = open-access records only.
- Some publisher PDFs are named `…-main.pdf__tid=…`; `is_pdf_name` accepts them.

## OpenAlex enrichment results (`chatcde harvest openalex`, 2026-11-07)

- `Record.doi` is **BORIS's own DataCite DOI** (10.7892, 10.48350, 10.48620); the
  publisher DOI is `dc.identifier.publisherDOI` (981 records), read from the raw cache.
- **210 of 1,665 missing abstracts filled** (abstracts now 1,548 / 3,003); 606 works
  have no abstract in OpenAlex, 602 records have no DOI at all (decision 0010).
- 1,698 OA links in `data/raw/openalex/oa_links.csv`; 183 point outside BORIS for
  records whose BORIS file was restricted or missing.
- OpenAlex now uses API keys and a daily budget instead of the mailto polite pool; a
  full run costs ~$0.003 and needs no key.

## Open extraction results (`chatcde vocab extract`, 2026-11-08)

- Sample: **250 of 2,148 eligible records** (abstract or page text) in 29 unit x decade x
  language strata, seed 13, floor 3. 78 have a CDE unit, 54 are German, 27 other
  languages, 8 pre-2000; 239 have page text (decision 0019).
- Labels (qwen3.8-27b, `vocab_extract.v1`): 250/250 records, 0 failures, 4,350 labels
  (method 786, theme 1,203, data_type 492, setting 516, system 552, actors 801);
  **98% of quotes found** in the input text. About 20 min at 4 parallel requests.
- Propose (qwen3-embedding-4b, average linkage at 0.80, min 3 records):
  **353 candidates** in `vocab/candidates.yaml` (method 55, theme 86, data_type 42,
  setting 40, system 57, actors 73); 638 proposals cached, about 8 min.
- Counts in candidates.yaml describe the oversampled sample, not corpus shares.
- The model still puts some country names under settings ("Switzerland", "Global South").

## Architecture

```
BORIS / OpenAlex
  → pipeline: chatcde harvest → parse (+OCR) → tag → index → evaluate
  → stores in data/: catalogue (DuckDB) | text index (vectors + BM25) | field corpus
  → chatcde.tools: plain typed Python functions (the core)
  → interfaces: chatcde CLI (now); web app or MCP server (later, same functions)
  → agent: local LLM via an OpenAI-compatible API, tool definitions generated
    from the tool functions' signatures and docstrings
```

Suggested stack (open to change; record changes as decisions): Typer + Rich for
the CLI, `httpx`, DuckDB for the catalogue, a multilingual embedding model
(bge-m3 or multilingual-e5) run locally, hybrid search (vectors + BM25), PyMuPDF
or docling for PDFs plus OCR for scans, the `openai` client pointed at the local
endpoint, pydantic for schemas and config, pytest, ruff.

## Models (GPUStack, OpenAI-compatible)

Set by role in `config/default.yaml`; see `docs/decisions/0003-models-by-role.md`.
Endpoint `https://gpustack.unibe.ch/v1`, about 4 parallel requests per model
(verified 2026-11-06 with `scripts/check_gpustack.py`; minimax-m2.7 is not served).
Agent: gpt-oss-120b (alt. qwen3.8-27b). Tagging: qwen3.8-27b with thinking off
(alt. lfm2.5-24b-a2b).
Embeddings: qwen3-embedding-4b (alt. granite-embedding-107m-multilingual).
Reranker: jina-reranker-v2-base-multilingual (1K context → ~500-token chunks).
Vision for scans/figures: qwen3-vl-30b-a3b-instruct. Optional speech: faster-whisper-large-v3.
Code must never hard-code a model name; always read the role from config.

## Data editions

See `docs/decisions/0004-two-data-editions.md`. One build, two exports:
- **full**: all full texts; hosted on CDE's server, reachable only from the UniBe
  network/VPN. URL is a **placeholder** in `config/default.yaml` (TODO).
- **open**: full text only for open-access records, metadata + abstracts for the rest;
  hosted publicly.
`chatcde fetch-data` probes the full URL (short timeout) and falls back to open;
`--edition full|open` overrides; `--top-up` (fallback design) downloads remaining
PDFs from BORIS under the user's own access and indexes them locally.
Each bundle carries a manifest (edition, version, build date, vocab/model versions,
counts, checksums); `chatcde info` and `coverage_report` report the edition.

## Command line (target)

| Command | What it does |
|---|---|
| `chatcde ask "…"` / `chatcde chat` | Free questions; the agent calls tools and answers with citations |
| `chatcde gaps`, `frontier`, `brief`, `experts` | Fixed workflows with a set tool chain and output format |
| `chatcde search`, `count`, `vocab`, `schema`, `coverage`, `info` | Direct tool calls, no LLM |
| `chatcde harvest`, `parse`, `tag`, `index`, `evaluate` | Pipeline stages |
| `chatcde vocab extract` | Open extraction on the sample, for vocabulary drafting |
| `chatcde fetch-data [--edition full\|open] [--top-up]` | Download a prebuilt data bundle (full on VPN, else open) |
| `chatcde export --edition full\|open` | Build a distributable bundle from the local index |

## Schema (17 dimensions, draft)

Full definitions and scope notes are in `docs/schema_draft.md` and `vocab/draft_vocabulary.yaml`.

| # | Dimension | Source |
|---|---|---|
| 1 | impact_area | Strategy 2025–2028, Table 1 (JE, SL, SG, TE) |
| 2 | sub_area | Strategy Table 1 (16 sub-areas, e.g. SL-d) |
| 3 | objective | Strategy 1.4 (O1–O5) |
| 4 | pathway | Strategy 2.2 (P1–P5), only when explicit |
| 5 | research_mode | Strategy 1.3 (M1–M4) |
| 6 | transformative_aspiration | Westerink et al. 2026 |
| 7 | method | proposed 34 terms in 8 groups |
| 8 | data_type | proposed |
| 9 | country | ISO 3166-1 alpha-3 |
| 10 | region | derived: UN M49 + CDE partner regions |
| 11 | setting | proposed (mountains, drylands, …) |
| 12 | scale | proposed |
| 13 | system | proposed + strategy terms |
| 14 | actors | proposed + strategy terms, each with a role: studied / co-producer / audience |
| 15 | justice_lens | strategy-derived |
| 16 | sdg | UN 17 goals, max 3 |
| 17 | output_type | BORIS type + audience |

Vocabulary process (no workshops): seed from the YAML; the local LLM does open
extraction on a stratified sample of 200–300 records (by unit, decade and language); the developer
curates the YAML and commits with a changelog; the LLM tags with
confidence + evidence quote; the developer hand-tags a reference set of about 50
records; `chatcde evaluate` reports precision/recall against it and agreement
between two LLM runs.

## Tools (19, by phase)

- **Prototype core:** `search_publications`, `get_publication`, `read_section`,
  `list_vocabulary`, `count_by`, `find_gaps`, `coverage_report`
- **If time allows for the demonstration:** `trend`, `find_expertise`, `search_field`,
  `compare_with_field`
- **Later:** `find_similar`, `emerging_topics`, `get_project`, `spatial_footprint`,
  `report_issue`, `collaboration_network`, `find_datasets`, `query_layer`

## Layout

```
CONTRIBUTING.md       tracks, git/GitHub workflow, setup how-tos
.github/              CODEOWNERS, PR template, CI (ruff + pytest)
pyproject.toml
config/default.yaml   all settings; placeholders marked TODO
src/chatcde/
  cli.py          root Typer app: global options; assembles each subpackage's cli.py
  cli_common.py   help panels and the stub helper shared by the sub-apps
  config.py       loads and validates config (pydantic); see decision 0006
  log.py          logging setup (Rich console on stderr + rotating file)
  schemas.py      shared data formats: the contract between tracks (DRAFT)
  harvest/        BORIS REST client, OpenAlex enrichment, PDF download, manifest
  parse/          PDF → text (+ OCR), chunking
  tag/            LLM extraction against the vocabulary; vocab drafting
  store/          catalogue (DuckDB), text index
  tools/          the tool functions (core logic, no LLM code); core.py = signatures,
                  cli.py = thin CLI wrappers (each subpackage has its own cli.py)
  agent.py        LLM loop over the OpenAI-compatible API
  workflows/      ask, chat, gaps, frontier, brief, experts
  bundle/         data bundles: export, fetch-data, info
  prompts/        versioned prompt templates
vocab/            versioned vocabularies (YAML) + CHANGELOG.md
docs/             planning.md, schema_draft.md, decisions/
tests/            unit tests + questions.yaml (test questions with known answers)
scripts/          one-off scripts: boris_cde_inventory.py, create_github_issues.py + issues.yaml
data/             local only; gitignored (raw/, text/, index/, catalogue.duckdb)
```

## Build steps

1. **Package skeleton:** pyproject, config, CLI, logging, ruff, pytest.
   Check: `pip install -e .` works and `chatcde --help` lists the commands.
2. **Harvest and parse:** port `scripts/boris_cde_inventory.py` into
   `chatcde.harvest` (fix field names from `data/cde_fields.txt` first); download
   PDFs with a manifest (downloaded / restricted / missing); OpenAlex abstracts;
   parse + OCR. Check: `chatcde coverage`.
3. **Vocabulary and tagging:** see above. Check: `chatcde evaluate` meets the
   threshold recorded in a decision file.
4. **Tools, agent, CLI:** the core tools, the agent loop, `chatcde gaps`.
   Check: passes 20–30 questions in `tests/questions.yaml`.
5. **Demonstration:** extra tools if time allows, `chatcde export` for both editions,
   `chatcde fetch-data`, README walkthrough, demo script.

## Open questions

- Exact location of the full edition on CDE's server (placeholder in config).
- Does the University Library confirm that restricted publisher texts may be
  copied to staff machines (full edition)? If not, use `--top-up`.
- ~~Can files marked "restricted" in BORIS be downloaded on the VPN?~~ No (decision 0009).
- Older work: tag with 2025–2028 categories? (Proposal: yes, keeping the BORIS unit as recorded. Record as a decision.)
- Are there four or five CDE partner regions, and which countries belong to each? (Record as a decision.)
