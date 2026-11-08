# 0028: Tagging the corpus: terms only, in three batches, quotes checked by code

- Status: accepted (pilot pending; the full run waits for PR #55 and the pilot review)
- Date: 2026-11-09

## Context
Issue #15 tags all 3,003 records against vocabulary v0.3 (decisions 0026, 0027) so that
`count_by` and `find_gaps` have data. The v0.3 closed lists are long: method 60,
system 66, actors 61, additional_sub_area 77, data_type 44, setting 27. One request
with all 18 dimensions would put about 20,000 tokens of vocabulary in every prompt.
About half the records have full text, a fifth only an abstract, and 28% only
metadata. The tagging model (qwen3.8-27b, decision 0003) runs with about 4 requests
in parallel. The plan was agreed on #15 (comment of 2026-11-08).

## Decision
- **Tag terms only.** The model never assigns a group, an impact area or a region.
  - Groups are derived at query time from the vocabulary (#57).
  - Region is derived from country (decision 0017).
  - impact_area is derived from the parents of the sub_area and additional_sub_area
    tags (#49).
  - Regrouping or a new parent in a later vocabulary version then needs no
    re-tagging.
- **Three batches per record** (`tagging.batches`), one request each:
  - strategy: sub_area, additional_sub_area, objective, pathway, research_mode,
    transformative_aspiration;
  - how and where: method, data_type, setting, scale, country;
  - who and what: system, actors, justice_lens, sdg, output_type (audience).

  Each batch prompt is about 6–7k tokens of rules and terms, the same for every
  record, so the server's prefix cache absorbs most of it. A change to one dimension
  re-runs only its batch.
- **A JSON schema per batch** (`chatcde.tag.answer`):
  - `term` is an enum of the live codes, so retired codes and group codes cannot be
    written. Countries are the 248 alpha-3 codes of UN M49 (`vocab/m49.csv`) plus
    `global_or_multi_country`.
  - Curated dimensions also allow `other` with a short label, which feeds the next
    vocabulary version.
  - Lists are capped (`tagging.max_per_dimension`, e.g. sdg 3).
  - Actors need a role.
- **Terms are shown under their groups** in the prompt, with a definition and up to 6
  synonyms (`includes`) each. Sub-areas appear with their scope notes and under their
  impact areas; additional sub-areas under their `impact_area`.
- **The model reports evidence strength, code sets the confidence.**
  - explicit → 0.9, inferred → 0.6, weak → 0.3 (`tagging.confidence`), following the
    fixture convention.
  - Self-reported decimals are poorly calibrated, and a mapping can be retuned after
    #16 without re-tagging.
- **Quotes are checked by code.**
  - Each quote is looked up in the abstract, the page text and the metadata with the
    same tolerant match as open extraction (0019), and `Tag.source` records where it
    was found.
  - A quote found nowhere caps the tag at `tagging.unverified_cap` (0.4). That is below
    the 0.5 the tools count, so the tag stays visible for review but never enters a
    count.
- **Every record is tagged, metadata-only ones too.**
  - Counts keep the whole corpus as denominator, and `coverage_report` discloses
    what the text was.
  - Titles show sub-areas and countries reasonably well; the prompt asks for tags only
    where the title or keywords clearly show them.
- **Runs are kept apart from the published tags.**
  - `tag_runs` holds every run (`main`, `alt` with `model_alternatives.tagging`, or
    any name), and `tag_state` holds one row per record × batch × run with the model,
    prompt and vocabulary versions.
  - `chatcde tag` publishes one run to the shared `tags` table, with region and
    impact_area added in the same transaction (`chatcde.tag.derive`); by default
    that is `main`.
  - #16 compares runs (agreement, alternative model) from `tag_runs`.
- **Region derivation**, per country tag:
  - the finest UN M49 grouping (intermediate region if there is one, e.g. "Eastern
    Africa"; otherwise the sub-region, e.g. "Southern Asia");
  - the CDE partner region code if the country is in one;
  - `cde_partner_country` for the seven partner countries.
  - All countries get the M49 term; decision 0017 asked for both layers.
- **Resumable and idempotent.**
  - A record × batch already `ok` with the same model, prompt and vocabulary version
    is skipped; `--force` redoes it.
  - Errors are retried on the next run, and the stage stops after
    `max_consecutive_failures` endpoint errors.
- **A pilot before the full run.**
  - `chatcde tag --pilot` draws 100 records (`tagging.pilot`, seed 29), stratified by
    unit × decade × language like the vocabulary sample.
  - Every record is eligible, the 250 records of the vocabulary sample (#13) are
    excluded, and the order within a stratum is random.
  - These records later seed the #16 reference set, so precision is not measured on
    the records the vocabulary was built from.
  - The first draw has 40 records with full text, 22 with only an abstract and 38
    with only metadata. The unit floors over-represent metadata-only records compared
    with the corpus (28%).
- **No accidental full runs.** `chatcde tag` needs exactly one of `--pilot`, `--ids` or
  `--all`.

## Consequences
- **Cost:**
  - The full run is about 9,000 requests (3,003 × 3).
  - At the open-extraction rate (about 12 requests a minute at 4 parallel), that is
    roughly half a day to a day.
  - It is resumable, so it can run overnight in parts.
- Confidence has only three values from the model, plus 0.4 for unverified quotes.
  The tools' 0.5 threshold therefore counts explicit and inferred tags.
- The hand-made fixture region tags (#3) list either the partner region or the M49
  name, not both, and miss some countries. Derived regions on real data follow this
  decision; the fixtures are unchanged.
- Revisit:
  - **after the pilot:** the prompt (v2), the per-dimension caps, the confidence
    mapping, and whether metadata-only records give usable tags;
  - **after #16:** the threshold per dimension, and whether the alternative model
    agrees enough to serve as a check;
  - whether page selection (now the first 12,000 characters) should skip front matter.
