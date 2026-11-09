# 0029: Pilot review: vocabulary v0.4 and prompt tag.v3

- Status: proposed (the curator reviews `vocab/curation/v0.4.xlsx`, then pilot 3)
- Date: 2026-11-09

## Context
The curator hand-checked pilot 2 (decision 0028) in `data/reference/review_main.xlsx`:
20 records, 415 tags.

| | ok | wrong | unsure |
|---|---:|---:|---:|
| all tags | 353 | 14 | 48 |
| counted (confidence >= 0.5) | 304 | 9 | 45 |
| confidence 0.9 (explicit) | 95% | 1% | 4% |
| confidence 0.6 (inferred) | 68% | 5% | 26% |

- Weak dimensions:
  - research_mode: 11 of 15 unsure; M2 was a default, even for ecology papers.
  - scale: 5 of 21 wrong.
  - data_type: 7 of 35 not ok.
  - actors: the authors (CDE) were tagged, and funders were tagged as co-producers.
- Country, pathway, system and additional_sub_area were almost all ok.
- Many unsure tags had a quote that was found in the text but did not support the tag.
- **Recall was not checked systematically.** The curator judged the given tags and
  added only 5 missing ones (4 SDGs). Recall is left to the #16 reference set.

## Decision
- **Vocabulary v0.4** (`vocab/curation/v0.4.xlsx`; CHANGELOG). Every change carries a
  note citing the pilot record.
  - **Definitions** for every term: the 8 scale and 7 justice-lens terms, and the
    37 other terms that had none (drafted by Claude for the curator to review).
  - **research_mode:** clearer labels and definitions for M1–M4 (codes unchanged).
  - **Actors:**
    - `ACT-researchers-and-universities` excludes the authors and their institutes. Its
      includes no longer list "centre for development and environment".
    - Funder terms apply only when the work is about the funders.
    - `ACT-national-and-local-government` becomes "National and regional
      government". Cities go to `ACT-municipal-and-local-governments`. The code stays,
      because no corpus tags exist yet.
  - **Data types:**
    - `DAT-visual-data` becomes `DAT-visual-audio-data`, "Visual and audio data". The
      old code is retired with `replaced_by`. `vocab apply` now does this whenever a
      kept term gets a new code.
    - `DAT-field-data` covers social field observation only.
  - **New terms and synonyms:**
    - New additional sub-area `ASA-natural-hazards-and-disaster-risk`.
    - "water security" is a synonym of the water-management terms.
  - **Objectives:** the codes and labels stay as the Strategy has them. The curator
    asked about splitting O2 and SG-d, but those lists are fixed. They get
    definitions instead, which are proposals to check against Strategy 1.4.
- **"None explicit" is filled in by code** (`tagging.none_terms`, `derive.v2`). When
  asked directly, the model gave `JUS-none-explicit` with weak evidence (0.3), which
  says nothing.
  - At publish time, a record gets it when the model read its abstract or full text
    and no justice lens counts.
  - Title-only records do not get it, because absence says nothing there.
  - The model is no longer offered the term.
- **Prompt tag.v3.** It adds these rules:
  - Never tag the authors, their institutes or funders as actors.
  - The three actor roles are defined.
  - The quote must contain the words that justify the tag.
  - data_type covers only data the work itself collects or analyses.
  - research_mode may be empty.
  - Scale has examples.
  - Tag nothing the work only contrasts itself with.
- **Curation tooling** (decision 0026, extended):
  - An editable `includes` column.
  - Rows added by the curator (`source: curator`).
  - A `strategy_terms` sheet for the labels and definitions of the fixed strategy
    lists.
  - `--carry-from` an earlier round carries only open candidates.
- **The output_type → audience_type rename** is proposed in #60 (`schemas`). It
  changes the shared contract, so Track B must agree.

## Consequences
- Pilot 3 reruns the same 100 records with v0.4 and tag.v3. Only changed tags need a
  second look.
- Rewritten strategy blocks (objective, research_mode) are now in block-style YAML.
- Still open:
  - Three pairs of terms share a definition and may be duplicates (CHANGELOG 0.4).
  - data_type, method, system, actors and additional_sub_area exceed 40 terms.
  - TA0 ("None explicit" under transformative aspiration) could also be filled in
    by code.
- Revisit:
  - after pilot 3: whether inferred tags in research_mode, scale and data_type should
    count;
  - after #16: recall, especially for SDGs.
