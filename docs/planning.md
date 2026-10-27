# CDE Research Assistant: MCP Tools and Use Cases

Exported 2026-10-27 from the planning doc (https://claude.ai/artifact/93ZrZB6GgHHQDUUsyCtKvi).
For the build, this repo copy is the reference; discussion happens in the doc. Re-export when the doc changes in substance.

## Purpose and architecture

An MCP server sits between a local LLM and five local stores built from BORIS and related sources. The model handles the questions, and the tools supply the evidence, counts and citations. Nothing leaves UniBe infrastructure: Ubelix/GPUStack or a CDE workstation.

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
  MCP server: 19 tools in six groups
  (search, gap analysis, field comparison, people and projects, geodata, curation)
        ^  tool calls and results
        v
  Local LLM and chat client (answers with counts and BORIS citations)
        ^  questions and cited answers
        v
  CDE researchers
```

Only the harvest reaches outside the university. The pipeline, stores, MCP server, model and every question stay on UniBe machines.

## Design principles

The tools do the counting and the model does the interpreting.

1. **Numbers come from tools.** Every count, share or trend in an answer is a tool's output, and the answer shows that output.
2. **Every claim is traceable.** Tools return BORIS handles or DOIs with each result, and answers cite them.
3. **No absence claims without a query.** Calling something "rare" or "missing" requires a catalogue count and its denominator, not the absence of search hits.
4. **Coverage is disclosed.** Tools report how many records lack full text or reliable tags, so missing data isn't mistaken for a research gap.
5. **Read-only by default.** No tool writes to BORIS or changes the vocabulary. Only `report_issue` writes, and it writes to a local curation log that people review.
6. **Few, clear tools.** Mid-sized local models choose tools less reliably than frontier models. Start with the core tools of phase 1, give each one plain arguments and a precise description, and add others only when a use case needs them.

## Tool catalogue

The catalogue has 19 tools in six groups. Seven are needed for the phase 1 pilot. All filters share one set: year range, CDE unit, publication type, language, region, theme, method and SDG.

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

## Prompts and resources

MCP prompts script the core workflows, so a mid-sized model follows the same tool chain and output format every time. Resources give the model reference material without a tool call.

**Prompts** (each one fixes the tool chain and ends with an evidence table plus caveats):

- `gap_scan(scope, dimensions)`: runs the method and topic gap workflow for a unit, theme or region.
- `frontier_brief(strength)`: CDE's record in an area, how the field is moving, and two or three candidate directions.
- `literature_brief(topic, region?)`: a cited synthesis of CDE findings.
- `expertise_lookup(topic)`: people, units and projects, with reasons.
- `proposal_background(call_text)`: relevant prior work and a track-record paragraph.

**Resources:**

- `vocab://{dimension}`: the controlled vocabulary with definitions.
- `schema://catalogue`: what each extracted field means and how it was tagged.
- `stats://coverage`: a corpus snapshot by year, type, language and full-text availability.
- `cde://units` and `cde://glossary`: CDE's structure and key concepts.
- `field://definition`: the OpenAlex queries that built the field corpus.
- `log://harvests`: the date and scope of each harvest, so answers can state how current they are.

## Schema and vocabulary

People set the schema by hand. The LLM helps build each vocabulary, and people have the final say on it.

| Layer | What it is | Who builds it |
| --- | --- | --- |
| Schema | The dimensions (themes, methods, region, scale, data type, SDGs, stakeholders), each with a definition, whether a record can carry several values, and its granularity | Senior researchers, in a workshop of about two hours |
| Standard vocabularies | Region (ISO 3166 codes, UN M49 regions), SDGs, publication type (from BORIS) | Taken as they are; the LLM tags against them directly |
| Interpretive vocabularies | Themes, methods, transdisciplinary approach and similar | Drafted by the LLM from the corpus, curated by people |

Granularity is the main schema decision. A vocabulary that's too fine creates gaps that aren't real, and one that's too coarse hides the real ones.

**Building an interpretive vocabulary:**

1. **Seed (people).** Start from CDE's units and research clusters, terms already in use such as WOCAT categories, and OpenAlex's topic hierarchy as an outside reference.
2. **Open extraction (LLM).** On a stratified sample of 200–300 documents across units, decades and languages, the LLM writes free-text labels for methods and themes, without a fixed list.
3. **Draft terms (LLM).** Cluster the labels by embedding, then have the LLM propose candidate terms, definitions and merges.
4. **Curate (people).** Accept, merge, split or rename terms, and write a definition and an inclusion rule for each. Aim for 15–40 terms per dimension, with at most two levels.
5. **Tag the corpus (LLM).** Tag against the closed list, recording a confidence score and a short supporting quote per tag, with an "other: …" option when nothing fits.
6. **Validate (people).** Two people independently tag about 100 documents. Compare them with each other and with the LLM. Where agreement is low, revise the definition.
7. **Maintain (both).** Review the "other" bucket periodically, version every vocabulary change, and re-tag only the dimensions that change.

Neither extreme works. LLM-only categories drift between runs, reflect how the wider field frames things rather than CDE, and have no owner. Manual-only vocabularies are slow, miss what the corpus contains, and encode assumptions about CDE that the gap analysis should be testing.

**Pilot effort:** about two workshop sessions plus a few days of curation and gold-standard tagging, shared among two or three people. The LLM steps run overnight.

The first draft of the schema and vocabularies, built from the 2025–2028 strategy, is in [schema_draft.md](schema_draft.md); the machine-readable version is `vocab/draft_vocabulary.yaml`.

## Build order and open questions

The build runs in three phases. Each phase moves on only once its gate is passed.

1. **Pilot, one CDE unit.** Harvest, parse and tag that unit's publications, then build the seven phase 1 tools and the `gap_scan` prompt.
   - Gate: the unit agrees the gap tables are accurate, and the system passes 20–30 test questions with known answers.
2. **All of CDE, plus frontiers.** Extend to the full corpus and add the field corpus, trends, people and projects, spatial footprints and `report_issue`.
   - Gate: leadership agrees how gap and expertise results are framed and who can see them.
3. **Extras.** Add `collaboration_network`, `find_datasets` and `query_layer`, and schedule re-harvests.

**Open questions:**

- [x] What do the inventory results show? Answered 2026-10-27: 2,507 publications, ~70% with files attached, ~15 datasets (see CLAUDE.md).
- [ ] Which models does Ubelix/GPUStack serve with tool calling, and at what context length and concurrency?
- [ ] Which chat client will host the MCP server, and does it support MCP prompts and resources as well as tools?
- [ ] Who owns the controlled vocabulary and approves changes to it?
- [ ] Does the University Library confirm that restricted publisher PDFs may be indexed internally?
- [ ] Where is CDE's geodata held outside BORIS?
- [ ] Should staff be told about, or opt into, the expertise and collaboration tools?
