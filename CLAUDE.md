# CDE Research Assistant (local RAG + MCP over CDE's BORIS publications)

## What this project is

A local research assistant for the Centre for Development and Environment (CDE),
University of Bern. It indexes CDE's publications from BORIS Portal and lets
researchers ask a local LLM, via an MCP server, about:

1. **Gaps**: themes, methods, regions or actors CDE has rarely covered.
2. **Frontiers**: directions that build on CDE's strengths, compared with the wider field.
3. Other uses: literature synthesis, cross-country lessons, expertise finder,
   collaboration opportunities, proposal support, onboarding, SDG reporting.

Everything that touches full texts, prompts or answers runs **locally**: on UniBe's
Ubelix/GPUStack or a CDE workstation. Nothing is sent to external LLM APIs.

This file is the working summary. Details live in:
- `docs/planning.md`: architecture, design principles, each tool's inputs and
  outputs, use cases with tool chains, MCP prompts and resources, build phases.
  Read it before designing or changing any MCP tool.
- `docs/schema_draft.md`: the 16-dimension schema with scope notes and the
  workshop questions. `vocab/draft_vocabulary.yaml` is its machine-readable twin;
  keep them in sync.
For the build, these repo files are the reference. The original planning doc
(https://claude.ai/artifact/93ZrZB6GgHHQDUUsyCtKvi) is for discussion and is
re-exported here when it changes in substance.

## Hard rules

- **Privacy:** do not read full-text PDFs, extracted text, embeddings or RAG
  conversation logs into your context. Write code that processes them; inspect
  only counts, schemas, sample *metadata*, and errors. Public BORIS metadata
  (titles, abstracts, keywords) is fine to look at.
- **Counts come from code, never from the LLM.** Any claim that something is
  rare or missing must come from a catalogue query with its denominator.
- **Every answer is traceable** to BORIS IDs or DOIs.
- **Read-only towards BORIS.** Be gentle: about 0.3 s between requests, no parallel hammering.
- Never disable TLS verification. On macOS python.org builds, use `certifi`
  (already done in `scripts/boris_cde_inventory.py`).

## Data source facts (verified 2026-10-27)

- BORIS Portal = DSpace 7.6.1 + DSpace-CRIS.
  - REST API: `https://boris-portal.unibe.ch/server/api`. **This only works on the
    UniBe network or VPN.** Off-network it returns an HTML "Temporary Access
    Restriction" page with HTTP 200, so always check that the response is JSON.
  - OAI-PMH: `https://boris-portal.unibe.ch/server/oai/request` (public, but sets
    follow collections, not org units, so don't use it for CDE filtering).
  - Search: `/discover/search/objects?query="Centre for Development and Environment"&dsoType=ITEM`
    and then keep records whose affiliation/credit fields contain the CDE name.
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
  miscoded. A multilingual pipeline is required (at least EN + DE).
- **Only about 600 publications carry a CDE unit** (232 Just Economies & Human Well Being,
  214 Sustainable Governance, 188 Sustainable Land Systems, 81 Transformative
  Education & Science, 45 Services). The rest have none.
- Years: most output 2012–2025; about 270 publications before 2010.
- Known bug in the first run: access rights were merged with `dc.date.accessioned`.
  This is fixed in v3 of the script; re-run to get clean access counts.

## Architecture

```
BORIS / OpenAlex / other CDE sources
  → pipeline (harvest → parse + OCR → extract fields → normalise vocabulary → embed)
  → stores: text index | catalogue (SQLite/DuckDB) | field corpus | geodata | people graph
  → MCP server (tools, prompts, resources)
  → local LLM + chat client → CDE researchers
```

Suggested stack (open to change): Python 3.12+, `httpx`, DuckDB or SQLite for the
catalogue, a multilingual embedding model (bge-m3 or multilingual-e5), hybrid search
(vectors + BM25), PDF parsing with docling or PyMuPDF plus OCR for scans, the
official MCP Python SDK for the server. The LLM is served by GPUStack/Ubelix or a
local runtime; check which models support tool calling.

## Schema (16 dimensions, draft)

Full definitions and scope notes are in `docs/schema_draft.md` and `vocab/draft_vocabulary.yaml`.

| # | Dimension | Source |
|---|---|---|
| 1 | impact_area | Strategy 2025–2028, Table 1 (JE, SL, SG, TE) |
| 2 | sub_area | Strategy Table 1 (16 sub-areas, e.g. SL-d) |
| 3 | objective | Strategy 1.4 (O1–O5) |
| 4 | pathway | Strategy 2.2 (P1–P5), only when explicit |
| 5 | research_mode | Strategy 1.3 (M1–M4) |
| 6 | method | proposed 34 terms in 8 groups |
| 7 | data_type | proposed |
| 8 | country | ISO 3166-1 alpha-3 |
| 9 | region | derived: UN M49 + CDE partner regions |
| 10 | setting | proposed (mountains, drylands, …) |
| 11 | scale | proposed |
| 12 | system | proposed + strategy terms |
| 13 | actors | proposed + strategy terms, each with a role: studied / co-producer / audience |
| 14 | justice_lens | strategy-derived |
| 15 | sdg | UN 17 goals, max 3 |
| 16 | output_type | BORIS type + audience |

Vocabulary process: people set the schema; the LLM does open extraction on a
stratified sample of 200–300 records (by unit, decade and language), people curate, the LLM
does closed-set tagging with confidence + evidence quote, and people validate on
about 100 gold records.

## MCP tools (19, by phase)

- **Phase 1 (pilot):** `search_publications`, `get_publication`, `read_section`,
  `list_vocabulary`, `count_by`, `find_gaps`, `coverage_report`
- **Phase 2:** `find_similar`, `trend`, `search_field`, `compare_with_field`,
  `emerging_topics`, `find_expertise`, `get_project`, `spatial_footprint`,
  `report_issue`
- **Phase 3:** `collaboration_network`, `find_datasets`, `query_layer`

Prompts: `gap_scan`, `frontier_brief`, `literature_brief`, `expertise_lookup`,
`proposal_background`. Resources: `vocab://{dimension}`, `schema://catalogue`,
`stats://coverage`, `cde://units`, `cde://glossary`, `field://definition`, `log://harvests`.

## Suggested layout

```
docs/           planning.md, schema_draft.md
scripts/        one-off scripts (boris_cde_inventory.py lives here)
src/cde_rag/
  harvest/      BORIS REST client, OpenAlex enrichment, PDF download
  parse/        PDF → text (+ OCR), chunking
  tag/          LLM extraction + vocabulary normalisation
  store/        catalogue DB, vector index
  mcp_server/   tools, prompts, resources
vocab/          versioned vocabularies (YAML)
data/           local only; gitignored (raw/, text/, index/)
tests/
```

`data/` must be in `.gitignore`. Never commit PDFs, extracted text or indices.

## Next steps

1. Re-run `scripts/boris_cde_inventory.py` (v3, no `--check-files`) on VPN →
   `cde_records.csv`, `cde_summary.txt`, `cde_fields.txt`. Fix field names from
   `cde_fields.txt`.
2. Test the draft vocabulary against titles, abstracts and keywords: hit counts per
   term, terms that never match, frequent keywords that match no term.
3. Build the harvester: full metadata + PDF download for the pilot set, resumable,
   with a manifest recording downloaded / restricted / missing for each record.
4. OpenAlex enrichment for DOIs without abstracts (and open-access PDF links).
5. Pilot scope: the ~600 publications with a CDE unit (the unit gives impact-area
   tags something to check against), or one unit only.
6. Parse → chunk → embed; catalogue tagging on the pilot; phase 1 MCP tools.

## Open questions (owner: project lead)

- Which models does Ubelix/GPUStack serve with tool calling, and at what context length?
- Which chat client hosts the MCP server, and does it support prompts and resources?
- Does the University Library allow indexing restricted publisher PDFs internally?
- Are there four or five CDE partner regions, and which countries belong to each?
- Should older work be tagged with 2025–2028 categories? (Proposal: yes, keeping the BORIS unit as recorded.)
- Where is CDE's geodata held outside BORIS?
