# ChatCDE: Tools and Use Cases (prototype)

Exported 2026-10-27 from the planning doc (https://claude.ai/artifact/93ZrZB6GgHHQDUUsyCtKvi).
For the build, this repo copy is the reference; discussion happens in the doc. Re-export when the doc changes in substance.

## Purpose and architecture

ChatCDE is a prototype: a Python package with a command-line interface that answers questions about CDE's publications. Its tools query five local stores built from BORIS and related sources, and a local LLM, reached through an OpenAI-compatible endpoint, answers questions by calling those tools. The publications are public, so the build has no privacy constraints. Every LLM call, both in the pipeline and when researchers ask questions, goes to a local model on Ubelix/GPUStack or a CDE workstation.

- **Text index:** full text and abstracts, chunked and embedded with a multilingual model such as bge-m3, searched by vectors and keywords together.
- **Catalogue:** one row per publication, holding the BORIS metadata plus extracted fields (themes, methods, regions, scale, data types, SDGs, stakeholder groups) mapped to a controlled vocabulary.
- **Field corpus:** OpenAlex metadata and abstracts on CDE's core topics, tagged with the same schema, for comparing CDE with the wider field.
- **Geodata:** each publication's study-area footprint, plus CDE datasets from BORIS and elsewhere, in PostGIS or GeoPackages.
- **People graph:** researchers, units, projects and co-authorship, taken from BORIS.

```
  BORIS Portal          OpenAlex              Other CDE sources
  (metadata, PDFs,      (field corpus on      (website PDFs,
   datasets)             CDE topics)           geodata)
        |                     |                     |
========|=====================|=====================|====  UniBe infrastructure
        v                     v                     v      (Ubelix/GPUStack or
  Pipeline, run on a schedule:                               a CDE workstation)
  harvest -> parse and OCR -> extract fields -> normalise vocabulary -> embed
        |
        v
  Text index | Catalogue | Field corpus | Geodata | People graph
        |
        v
  Tool layer: 19 Python functions in six groups
  (search, gap analysis, field comparison, people and projects, geodata, curation)
        ^  tool calls and results
        v
  chatcde command line + local LLM (answers with counts and BORIS citations)
        ^  questions and cited answers
        v
  CDE researchers
```

Only the harvest reaches outside the university. The pipeline, stores, tools, model and every question stay on UniBe machines.

## Interface and distribution

Researchers use ChatCDE from the command line. The tools are plain Python functions, so a web app or an MCP server can be added later without rewriting them.

| Command | What it does |
| --- | --- |
| `chatcde ask "…"` | Free question; the local LLM chooses and calls the tools, then answers with citations |
| `chatcde chat` | Interactive session in the terminal, keeping context between questions |
| `chatcde gaps`, `chatcde frontier`, `chatcde brief`, `chatcde experts` | Fixed workflows with a set tool chain and output format |
| `chatcde search`, `chatcde count`, `chatcde vocab`, `chatcde coverage` | Direct tool calls, without the LLM |
| `chatcde harvest`, `parse`, `tag`, `index`, `evaluate` | Build pipeline stages, each resumable and logged |
| `chatcde fetch-data` | Download a prebuilt data bundle instead of running the pipeline |

The code and the data are distributed separately:

- **Package:** installed with `pip install git+https://github.com/nmkearney/CAS-AML-Module3`, built from `pyproject.toml`, with `chatcde` as its command.
- **Data bundles:** the catalogue, search index and vocabulary as a versioned archive, in two editions built from the same index:
  - **Full:** all full texts. Hosted on CDE's server, reachable only from the UniBe network or VPN (location to be confirmed).
  - **Open:** full text only for open-access records; metadata and abstracts for the rest. Hosted publicly.
  - `chatcde fetch-data` tries the full edition and falls back to the open one; `chatcde info` shows which edition is in use. If restricted texts may not be copied to staff machines, users get the open edition and run `chatcde fetch-data --top-up` on the VPN to fetch the remaining PDFs from BORIS themselves.
- **Configuration:** `config/default.yaml` sets the LLM endpoint, the model for each role, paths, bundle locations and the vocabulary version. The same code runs against GPUStack, vLLM or Ollama without changes.

**Models by role** (GPUStack; compared with `chatcde evaluate`):

| Role | First choice | Alternative |
| --- | --- | --- |
| Agent (tool calling, answers) | gpt-oss-120b | qwen3.8-27b |
| Bulk tagging (JSON output) | qwen3.8-27b (thinking off) | lfm2.5-24b-a2b |
| Embeddings | qwen3-embedding-4b | granite-embedding-107m-multilingual |
| Reranking | jina-reranker-v2-base-multilingual | — |
| Scanned pages and figures | qwen3-vl-30b-a3b-instruct | internvl3-8b-instruct |
| Audiovisual records (optional) | faster-whisper-large-v3 | — |

## Design principles

The tools do the counting and the model does the interpreting.

1. **Numbers come from tools.** Every count, share or trend in an answer is a tool's output, and the answer shows that output.
2. **Every claim is traceable.** Tools return BORIS handles or DOIs with each result, and answers cite them.
3. **No absence claims without a query.** Calling something "rare" or "missing" requires a catalogue count and its denominator, not the absence of search hits.
4. **Coverage is disclosed.** Tools report how many records lack full text or reliable tags, so missing data isn't mistaken for a research gap.
5. **Read-only by default.** No tool writes to BORIS or changes the vocabulary. Only `report_issue` writes, to a local curation log.
6. **Few, clear tools.** Mid-sized local models choose tools less reliably than frontier models. Start with the phase 1 tools, give each one plain arguments and a precise docstring, and add others only when a use case needs them.
7. **Everything is reviewable code.** Each step is a documented command or function, with type hints, docstrings, a README and tests. Notebooks are for exploring only, never part of the pipeline.
8. **Reproducible outputs.** Every output records the vocabulary version, prompt version and model that produced it, and every design choice is logged in `docs/decisions/`.
9. **Local LLM only.** All model calls go to a configurable OpenAI-compatible endpoint on UniBe hardware, with structured JSON output for tagging.

## Tool catalogue

The catalogue has 19 tools in six groups, seven of them needed for the first prototype. Each tool is a typed, documented Python function in `chatcde.tools`. The command line exposes it as a subcommand, and the LLM agent receives it as a function definition generated from its signature and docstring. All filters share one set: year range, CDE unit, publication type, language, region, theme, method and SDG.

### Search and reading

| Tool | Inputs | Returns | Phase |
| --- | --- | --- | --- |
| `search_publications` | query, filters, k | ranked passages with BORIS ID, title, year, page and score | 1 |
| `get_publication` | BORIS ID or DOI | full metadata, abstract, extracted tags with confidence, full-text availability | 1 |
| `read_section` | BORIS ID, page or section range | the text of that part, for drill-down and quotation | 1 |
| `find_similar` | BORIS ID or a text passage | nearest publications by document embedding | 2 |

### Gap analysis (catalogue)

| Tool | Inputs | Returns | Phase |
| --- | --- | --- | --- |
| `list_vocabulary` | dimension (theme, method, region…) | terms with definitions and record counts | 1 |
| `count_by` | one or two dimensions, filters | a count matrix, with the BORIS IDs behind each cell | 1 |
| `find_gaps` | row and column dimension, filters, threshold | thin cells, with row and column totals and the count expected if tags were independent | 1 |
| `coverage_report` | filters | shares with full text, abstract only and low-confidence tags, by year and language | 1 |
| `trend` | dimension values, period, bin size | counts and share of CDE output per period | 2 |

### Field comparison (frontiers)

| Tool | Inputs | Returns | Phase |
| --- | --- | --- | --- |
| `search_field` | query, filters | OpenAlex works with title, abstract, year and DOI | 2 |
| `compare_with_field` | dimension, filters, period | CDE share and field share per term, growth rates, over- and under-representation | 2 |
| `emerging_topics` | period, filters | fastest-growing terms in the field corpus, with CDE's count for each | 2 |

### People and projects

| Tool | Inputs | Returns | Phase |
| --- | --- | --- | --- |
| `find_expertise` | theme, method or region | researchers and units ranked by relevant outputs and recency, with BORIS IDs | 2 |
| `get_project` | project ID or query | project metadata, funders, partners and linked outputs | 2 |
| `collaboration_network` | unit or person, filters | co-authorship links, external partners, unit pairs with few joint outputs | 3 |

### Geodata

| Tool | Inputs | Returns | Phase |
| --- | --- | --- | --- |
| `spatial_footprint` | filters | publication counts by country or region, as a table or GeoJSON | 2 |
| `find_datasets` | query, country or bounding box, theme | datasets with format, access rights, DOI and linked publications | 3 |
| `query_layer` | dataset ID, area, attributes | summary statistics from a layer or table | 3 |

### Curation

| Tool | Inputs | Returns | Phase |
| --- | --- | --- | --- |
| `report_issue` | BORIS ID, field, note | a confirmation; the issue goes to the local curation log | 2 |

## Use cases

The two core uses, gaps and frontiers, rely on catalogue and field tools for their evidence. The other ten mostly run on search.

| Use case | Example question | Tool chain | What the chat adds |
| --- | --- | --- | --- |
| Method and topic gaps | Which methods have we rarely used in land-governance research? | `list_vocabulary` → `count_by` (theme × method) → `find_gaps` → `search_publications` on thin cells → `coverage_report` | Judges whether a gap is real, a tagging artefact or a deliberate choice |
| Regional gaps | Where have we worked on water governance, and where not? | `spatial_footprint` → `count_by` (region × theme) → `find_datasets` | Puts the map in context, such as partner countries and mandates |
| Frontiers | Where could our strength in telecoupling lead next? | `trend` → `compare_with_field` → `emerging_topics` → `search_field` | Proposes directions, clearly marked as suggestions with their evidence |
| Literature synthesis | What has CDE found on large-scale land acquisitions in Laos? | `search_publications` → `get_publication` → `read_section` | A cited synthesis |
| Cross-country lessons | What do our studies say about payments for ecosystem services across countries? | `count_by` (region) → `search_publications` per region | Compares findings and notes where contexts differ |
| Concept history | How has CDE's framing of sustainability transformations changed since 2000? | `trend` → `search_publications` by period | Narrates the shift, with dated citations |
| Expertise finder | Who has worked on pastoralism in East Africa? | `find_expertise` → `get_project` | A short list with reasons |
| Collaboration opportunities | Which units work on adjacent topics but rarely publish together? | `collaboration_network` → `count_by` (unit × theme) | Suggests pairings and shared questions |
| Proposal support | What prior CDE work supports a proposal on just energy transitions? | `search_publications` → `find_similar` → `get_project` → `find_expertise` | Drafts a background and track-record paragraph with citations |
| Onboarding | What does CDE mean by transdisciplinarity, and where should I start reading? | `search_publications` → `get_publication`, plus the glossary resource | A plain explanation and a reading list |
| Reporting | How do our 2025 outputs map to the SDGs? | `count_by` (SDG × year) → `trend` | Draft text for the annual report |
| Data discovery | Which CDE datasets cover land use in Myanmar? | `find_datasets` → `spatial_footprint` | Explains access conditions and the linked publications |

## Workflows and reference data

Fixed workflows script the core uses, so a mid-sized local model follows the same tool chain and output format every time. Each is a command with its own prompt template in `src/chatcde/prompts/`, versioned like the vocabulary. They map one-to-one to MCP prompts if an MCP server is added later.

| Command | Tool chain and output |
| --- | --- |
| `chatcde gaps --scope … --dims theme,method` | The method and topic gap workflow for a unit, theme or region; ends with an evidence table and coverage caveats; conference items are left out unless included (decision 0018) |
| `chatcde frontier --strength …` | CDE's record in an area, how the field is moving, and two or three candidate directions |
| `chatcde brief --topic … [--region …]` | A cited synthesis of CDE findings |
| `chatcde experts --topic …` | People, units and projects, with reasons |

Reference data is available both to the agent and on the command line:

- `chatcde vocab [dimension]`: the controlled vocabulary with definitions.
- `chatcde schema`: what each catalogue field means and how it was tagged.
- `chatcde coverage`: a corpus snapshot by year, type, language and full-text availability.
- `chatcde info`: data bundle version, harvest date, models and vocabulary version in use.

## Schema and vocabulary

In the prototype, the developer sets the schema and curates the vocabularies, and the local LLM drafts them from the corpus. Each choice is recorded in `docs/decisions/`, so a later phase can revisit it with CDE staff.

| Layer | What it is | Who builds it |
| --- | --- | --- |
| Schema | The 18 dimensions, each with a definition, whether a record can carry several values, and its granularity | Developer, from the 2025–2028 strategy; choices logged as decision records |
| Standard vocabularies | Country (ISO 3166), region (UN M49), SDGs, publication type (from BORIS) | Taken as they are; the LLM tags against them directly |
| Interpretive vocabularies | Sub-areas, methods, systems, actors and similar | Drafted by the local LLM from the corpus, curated by the developer |

Granularity is the main schema decision. A vocabulary that's too fine creates gaps that aren't real, and one that's too coarse hides the real ones.

**Building an interpretive vocabulary:**

1. **Seed (code).** Start from `vocab/draft_vocabulary.yaml`, derived from the strategy, with OpenAlex's topic hierarchy as an outside reference.
2. **Open extraction (LLM).** On a stratified sample of 200–300 records across units, decades and languages (at least English and German), the LLM writes free-text labels for methods and themes, and for the other dimensions with proposed seed lists (data types, settings, systems, actors): `chatcde vocab extract` (decision 0019).
3. **Draft terms (LLM).** Cluster the labels by embedding and have the LLM propose terms, definitions and merges into `vocab/candidates.yaml`.
4. **Curate (developer).** Accept, merge, split or rename terms in the YAML, then commit with a changelog entry. Aim for 15–40 terms per dimension, with at most two levels.
5. **Tag the corpus (LLM).** `chatcde tag` tags against the closed list using structured JSON output, recording a confidence score and a short supporting quote per tag, with an "other: …" option.
6. **Validate (developer and code).** The developer hand-tags a reference set of about 50 records. `chatcde evaluate` reports precision and recall per dimension against it, plus agreement between two LLM runs. Definitions with low scores are revised.
7. **Maintain.** Review the "other" bucket, version every vocabulary change, and re-tag only the dimensions that change.

**Prototype effort:** a few days of curation and reference tagging by the developer; the LLM steps run overnight. If the prototype goes further, staff review replaces developer review and the reference set grows.

The first draft of the schema and vocabularies, built from the 2025–2028 strategy, is in [schema_draft.md](schema_draft.md); the machine-readable version is `vocab/draft_vocabulary.yaml`.

## Team and workflow

Two developers work in parallel, split between building the data and using it. They meet at the shared data formats in `src/chatcde/schemas.py`, agreed on day 1 together with a small set of test fixtures, so Track B can build tools before the real data exists.

| | Track A: Data (Norman) | Track B: Assistant (Simon) |
| --- | --- | --- |
| Owns | Harvest, parsing, vocabulary, tagging, data bundles | Chunking, embeddings, search, tools, agent, workflows |
| Build steps | 2 and 3 | 4 |
| Cross-check of the other track | Writes the 20–30 test questions | Hand-tags part of the reference set |

Steps 1 and 5 are joint. All work goes through branches and pull requests approved by the other developer, tracked as GitHub issues by milestone (one per build step). `CONTRIBUTING.md` has the workflow and the GitHub setup steps; `scripts/create_github_issues.py` creates the labels, milestones and 29 issues from `scripts/issues.yaml`.

## Build order and open questions

The prototype covers all 2,507 CDE publications and is built in five steps, each ending with a check that can be run as a command.

1. **Package skeleton.** `pyproject.toml`, `config.yaml`, the `chatcde` command, logging, linting (ruff) and a test suite (pytest).
   - Check: `pip install -e .` works, `chatcde --help` lists the commands, tests pass.
2. **Harvest and parse.** BORIS metadata for every record, PDFs where available, OpenAlex abstracts for DOIs without one, then parsing with OCR for scans. A manifest records each record's status.
   - Check: `chatcde coverage` shows the full-text and abstract shares by year, type and language.
3. **Vocabulary and tagging.** Seed, extract, curate and tag as above, plus the reference set.
   - Check: `chatcde evaluate` meets the precision threshold set in a decision record.
4. **Tools, agent and command line.** The seven phase 1 tools, the agent loop and `chatcde gaps`.
   - Check: the system passes 20–30 test questions with known answers, kept in `tests/questions.yaml`.
5. **Demonstration.** Add `trend`, `find_expertise` and the field comparison if time allows; build the data bundle; write a README walkthrough and a demo script.

If the prototype succeeds, later work adds a web app or MCP server, staff review of the vocabulary, the geodata tools and scheduled re-harvests.

**Open questions:**

- [x] What do the inventory results show? 2,507 publications, about 70% with files attached, about 15 datasets.
- [x] How will researchers use it? Command line first; web app or MCP server later.
- [x] May the build use cloud models? No: every LLM call stays local.
- [x] Which models are available? GPUStack's list; roles assigned above.
- [x] Are tool calling and JSON-schema output enabled on GPUStack for gpt-oss-120b and qwen3.8-27b, and what is the concurrency limit? Yes, both work; about 4 parallel requests per model (decision 0003, checked 2026-11-06).
- [ ] Exact location of the full edition on CDE's server.
- [ ] Does the University Library confirm that restricted publisher texts may be copied to staff machines? If not, use `--top-up`.
- [x] Can files marked "restricted" in BORIS be downloaded on the VPN? **No**: 0 of 530, all HTTP 401 (2026-11-06, decision 0009).
- [ ] Where is CDE's geodata held outside BORIS? (Deferred until after the prototype.)
- [ ] If the prototype continues, who reviews the vocabulary, and should staff be told about the expertise tools?
