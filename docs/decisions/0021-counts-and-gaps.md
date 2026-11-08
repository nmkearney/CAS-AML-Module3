# 0021: Counting rules and gap detection

- Status: accepted
- Date: 2026-11-08

## Context
Issue #24 asks for `list_vocabulary`, `count_by` and `find_gaps`; Track A does
`coverage_report` in #11. CLAUDE.md requires that every claim that something is rare or
missing comes from code, with its denominator and the BORIS IDs behind it. Tags have
several values per dimension and a confidence. The vocabulary YAML writes each
dimension in its own shape. Decision 0018 leaves conference items out of gap counts by
default, without changing `Filters`. The tool signatures and result models
(`CountMatrix`, `GapCell`, `VocabTerm`) are the shared contract.

## Decision
- **Count records, not tags.** A record with two methods counts under both, so row
  totals can add up to more than the number of records. Only tags with
  `confidence >= filters.min_confidence` (0.5 by default) count. Filters resolve to
  record IDs with the same `filter_record_ids` as search (0013).
- **Denominators.**
  - `total_records`: the records that match the filters, tagged or not.
  - With one dimension, `untagged_records` is the matching records with no tag on it.
  - With two dimensions, cells, row totals and column totals count only the records
    tagged on *both*. `untagged_records` is everything else, so the base of the matrix
    is `total_records − untagged_records`. Each cell then reads "n of the base".
- **Cells:** only combinations that occur are listed. Missing pairs are 0, which keeps
  results small (16 sub-areas × 34 methods = 544 pairs). Each cell lists all its record
  IDs, sorted. Trimming long lists for the model's context is the agent loop's job (#25),
  not the tool's. Both dimensions may be the same in `count_by`, which gives
  co-occurrence counts.
- **Gaps:** for every pair of a row term and a column term seen in the base, including
  pairs with count 0:
  - `expected = row_total × col_total / base`, the count expected if the two
    dimensions were independent;
  - the pair is a gap when `count / expected <= max_ratio` (0.25 by default);
  - pairs with `expected < gaps.min_expected` (5) are not judged, because "0 of an
    expected 0.4" says nothing (the usual rule of thumb for χ² tests);
  - gaps are sorted by ratio, then by expected count, highest first.
  The two dimensions must differ.
- **Conference items (0018):** `gaps.exclude_types: [conference_item]`, the key Track A
  proposed in #46, is in `config/default.yaml`. If the filters name no publication types,
  `find_gaps` sets `pub_types` to every other type in the catalogue. Types named by the
  user are kept as they are. `count_by` counts all types. `gap_report` returns the gaps
  with their basis: the filters used, the excluded types and how many matching records
  they had. `chatcde count --gaps` prints this, and `--all-types` keeps conference items.
- **Vocabulary:** `chatcde.tools.vocabulary` reads every shape in the YAML. Codes are
  what `tags.term` stores. The SDGs get their 17 UN names. Partner regions get their
  countries or M49 region. Codes found in the tags but not in the vocabulary are listed
  after the vocabulary's terms, by count, with a note: `other`, ISO countries, M49
  sub-regions, or terms renamed since tagging. `record_count` is over the whole
  catalogue, at confidence ≥ 0.5.
- **CLI:**
  - `chatcde count ROW [COL]` takes the full set of filters, `--gaps`, `--max-ratio`,
    `--all-types`, `--ids` and `--json`;
  - `chatcde vocab -d DIM` lists the terms of one dimension;
  - `chatcde vocab` and `chatcde schema` show all 17 dimensions: number of terms, terms
    in use, and records tagged out of all records.

## Consequences
- **Excluded count not in the tool result.** `find_gaps` returns `list[GapCell]`
  (contract), so the count of excluded conference items does not reach the agent. The
  docstring says that conference items are left out, and the CLI and the gaps workflow
  (#27) use `gap_report`. A `GapReport` result model would fix this; propose it in a
  `schemas` issue if the agent needs it.
- **Two vocabulary readers.** Track A's tagging reader (#48, `chatcde.tag.vocabulary`)
  flattens terms to `"code: label"` strings for prompts. This one keeps code, label and
  definition apart. Merge them once both are on `main`.
- **Rare terms are never gaps.** With `min_expected = 5`, a term used by few records
  cannot appear in a gap. `list_vocabulary` shows such terms through their low
  `record_count` (0 = never covered).
- On the fixtures (34 records), no sub_area × method cell reaches 5 expected records, so
  the gap tests use a synthetic catalogue whose expected counts are worked out by hand.
- Revisit with the real tags:
  - `min_expected` and `max_ratio`;
  - whether to weight by confidence instead of using a cut-off;
  - a significance test (Poisson or χ²) instead of a ratio;
  - gaps over three dimensions (for example region × theme × method).
