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
  - **Duplicates:** three pairs with the same definition were merged into the older
    seed term (CHANGELOG 0.4).
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

## Pilot 3 (tag.v3, vocabulary 0.4, 2026-11-09)
- **Run:** the same 100 records, 300 requests in 12 minutes, 0 failures, 2% of quotes not
  found. Pilot 2 is kept as run `pilot_v2`. Impact areas still match BORIS units 19 of 26.
- **Across the 100 records, compared with pilot 2:**
  - actors: researchers tagged as co-producers fell from 28 to 9; funders are no longer
    co-producers.
  - research_mode: 58 → 44 tags; the model now leaves it empty for some works.
  - justice_lens: model tags fell from 110 to 61, while counted lenses rose from 50 to
    54; 24 "None explicit" tags were filled in by code.
  - scale: subnational 18 → 25, transboundary or regional 3 → 9, landscape 14 → 9.
  - objective: rose from 43 to 67 tags. The new definitions may make objectives too
    easy to tag, so check them in the re-review.
- **Re-review** (`chatcde evaluate review-sheet --carry-from`,
  `data/reference/review_pilot3.xlsx`):
  - The same 20 records give 412 tags. 345 had been judged before and keep their
    decision; 67 are new.
  - Of the earlier tags, 68 are gone: 8 marked wrong and 13 unsure, but also 47
    marked ok (`dropped` sheet). Most of the ok ones are researchers as
    co-producers and title-only "None explicit".
- **Still wrong after the rerun:**
  - scale on #8 and #13;
  - SDG 2 on #13;
  - "None explicit" on #1, filled in by code because the model again missed Power.
  - None of the 5 missing tags was added.

## Pilot 3 review (2026-11-09)
- The curator judged the 67 new tags: 61 ok, 2 wrong, 4 unsure. Objectives, which rose
  from 43 to 67 tags, were 8 of 10 ok, so the new definitions did not make them too
  loose.
- Counted tags in the 20 records: 324 ok, 5 wrong, 37 unsure (89% ok), up from 304, 9
  and 45 (85%) in pilot 2. Without the unsure ones: 98.5% against 97%.
- Most unsure tags were carried over unchanged from pilot 2 (research_mode M2, data
  collected or only cited, quotes that do not show the tag). The rerun did not change
  them.
- Still wrong:
  - scale on #8 (national, should be subnational) and #13 (global, should be
    transboundary or regional);
  - SDG 2 on #13;
  - O4 on #14;
  - "None explicit" on #1, because the model misses Power there.
- Recall: the 5 missing tags (4 SDGs, Procedural on #12) are still missing. Recall is
  measured with the #16 reference set.

## Recall check with a second model (2026-11-09)
- The curator has no time to check recall by hand. Instead, a second model tags the
  pilot, and the curator judges only the tags it gives that the main run does not
  (`chatcde evaluate recall-sheet`, `data/reference/recall_alt.xlsx`).
- **Second model:** the configured alternative, lfm2.5-24b-a2b, wrote explanations
  instead of quotes (100% not found), so it cannot serve as a check. gpt-oss-120b
  (the agent model, another model family) was used instead, with tag.v3,
  `reasoning_effort: low` and 8,192 answer tokens: 300 requests, 0 failures, 15% of
  quotes not found.
- **The configured alternative is now gpt-oss-120b** (`model_alternatives.tagging`),
  with its own `tagging.alternative` settings (decision 0003 updated).
- **Agreement on counted tags** across the 100 records (overlap = both / union):
  - country 0.76, research_mode 0.50;
  - most dimensions 0.31-0.44;
  - actors 0.21, transformative_aspiration 0.19.
- The models agree on countries but not on most closed-list dimensions. Counts per
  term therefore depend on the model. Gap claims need either more agreement or
  counts at group level (#57/#58), where small disagreements cancel out.
- The recall sheet lists 119 candidates on the 20 reviewed records. 10 are already
  judged ok: the main run gave them below 0.5. The second model found none of the 5
  tags the curator had listed as missing.

## Researchers as co-producers (curator's rule, 2026-11-09)
- In pilot 2 the curator marked researchers as co-producers ok on some records and wrong
  on others. The rule is now: **only researchers outside the author team count as
  co-producers**. The authors' own collaboration, and their institutes, never do.
- It is written into the definitions of `ACT-researchers-and-universities` and
  `ACT-research-institutions` (v0.4 workbook) and into prompt **tag.v4**, which the
  full run uses.
- tag.v3 already excluded the authors, so the rule makes tag.v3 explicit rather than
  changing it. Pilot 3 (tag.v3) stays as it is, so that its review measures that run.

## Consequences
- Pilot 3 reruns the same 100 records with v0.4 and tag.v3. Only changed tags need a
  second look.
- Rewritten strategy blocks (objective, research_mode) are now in block-style YAML.
- Still open:
  - data_type, method, system, actors and additional_sub_area exceed 40 terms.
  - TA0 ("None explicit" under transformative aspiration) could also be filled in
    by code.
- Revisit:
  - after pilot 3: whether inferred tags in research_mode, scale and data_type should
    count;
  - after #16: recall, especially for SDGs.
