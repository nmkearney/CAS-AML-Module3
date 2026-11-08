# Vocabulary changelog

Changes to `draft_vocabulary.yaml`, newest first. Each entry gives the version, the date,
what changed and why. Version numbers follow `version:` in the YAML and `vocab.version`
in `config/default.yaml`.

## 0.3 (2026-11-08)

Curated from the 353 open-extraction candidates in `vocab/candidates.yaml` (decision
0019) in `vocab/curation/v0.3.xlsx` by Norman Kearney, and written by
`chatcde vocab apply` (decision 0026). Issue #14.

- **Codes.** Every term in a curated dimension has a stable code (`MET-case-study`,
  `SET-mountains`, `ASA-food-security`); groups have codes too (`MET-G-…`). `Tag.term`
  stores the code. `vocab/curation/v0.3_codes.csv` maps every v0.2 label to its code.
- **New layout** for the curated dimensions: `groups`, `terms` (with `definition`,
  `includes`, `related`, `source`) and `retired`.
- **New dimension `additional_sub_area`** (decision 0027): 77 topics outside the strategy
  sub-areas, each with an `impact_area` (SL 26, cross_cutting 21, SG 11, JE 9, TE 5,
  outside 5). Most impact areas were drafted by Claude and reviewed by the curator.
  9 theme candidates were merged into sub-areas instead (their labels are now
  `includes` of JE-a, SL-b, SG-a, TE-a and TE-c) or into other terms.

| Dimension | Terms | Added | Merged in | Retired |
| --- | ---: | ---: | ---: | ---: |
| setting | 35 | 28 | 11 | 0 |
| data_type | 44 | 36 | 6 | 0 |
| method | 60 | 28 | 28 | 2 |
| system | 66 | 46 | 11 | 0 |
| actors | 61 | 44 | 21 | 0 |
| scale | 8 | 0 | 0 | 0 |
| justice_lens | 7 | 0 | 0 | 0 |
| additional_sub_area | 77 | 77 | 9 | 0 |

- **Groups:** every method, system and actor term has a group. Groups for the terms added
  in v0.3 were drafted by Claude and reviewed by the curator. The v0.2 group labels
  regained their "and" (e.g. "Synthesis and theory", code `MET-G-synthesis-and-theory`).
  New groups: `SYS-G-governance-and-institutions`, `ACT-G-public-and-households`, and six
  for data_type (`DAT-G-…`: documents, texts and media; primary social data; statistics and
  databases; geospatial and remote sensing; environmental measurements; no empirical
  data) and six for setting (`SET-G-…`: mountains and highlands; biomes and ecosystems;
  agricultural and rural landscapes; urban settings and institutions; protected and
  governed areas; places, regions and scales). Only scale and justice_lens stay flat.
- **Retired:** `MET-futures-methods` (no replacement; scenario work now goes to
  `MET-scenario-development-and-analysis`) and `MET-impact-evaluation` (merged into
  `MET-impact-and-sustainability-assessment`).
- **Known limits, for v0.4:**
  - data_type, method, system, actors and additional_sub_area are above the 15–40 target
    (additional_sub_area also above 0027's 25–35).
  - 52 terms have no definition, mostly v0.2 seeds (all of scale and justice_lens).
  - Some additional sub-areas overlap a sub-area's scope (e.g. telecoupling and SL-d).
  - Eight setting terms are places or scales (group `SET-G-places-regions-and-scales`:
    Africa, Switzerland, Global, …) and overlap country, region and scale.
  - data_type near-duplicates: *Documents and policies* / *Policy documents*,
    *Secondary statistics* / *Agricultural statistics*.
- **Fixtures:** `tests/fixtures/tags.jsonl` and `tag_review.csv` remapped from labels to
  codes (`scripts/remap_fixture_tags.py`); additional_sub_area tags added.
- `vocab/candidates.yaml` added: candidate terms from open extraction on a stratified
  sample of 250 records (`chatcde vocab extract`, decision 0019).

## 0.2 (2026-10-27)

- First draft from the CDE Strategy 2025–2028 plus proposed seed lists
  (see `docs/schema_draft.md`).
