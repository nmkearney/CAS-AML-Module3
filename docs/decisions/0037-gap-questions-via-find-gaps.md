# 0037: Gap questions go through find_gaps, step by step

- Status: accepted
- Date: 2026-11-11

## Context
On 2026-11-11, "Which methods has CDE rarely used in Sustainable Governance?" was
answered with the smallest of eight method-group shares (14%), which is not rare
(#67). The model also used a sub-area filter for SG instead of the impact area, and
summed sub-area counts into a denominator that counts records twice.

The known answer, from `find_gaps` on the real catalogue (tag.v5, vocabulary 0.4,
2,331 records without conference items): no SG cell is at or below 0.25 times its
expected count at impact-area level, by group or by term. Within SG, two sub-areas
are gaps: SG-b × geospatial and land (2 records, 14.3 expected) and SG-a ×
biophysical (7, 32.8).

Two things make the right call less obvious than it looks:
- **The comparison needs the other areas.** `find_gaps` compares a cell with what
  the other rows would predict. Filtering by SG leaves SG to compare with itself
  (and, until #83, the rows of other areas that SG records also carry).
- **An area-level check can miss a gap in one sub-area.**

## Decision
- **Prompt `agent_system.v4`** replaces the two gap lines of v3 with numbered steps:
  1. `find_gaps` with the area's dimension as the row and no filter on it;
  2. for an impact area, also `row="sub_area"`;
  3. the same at `level="term"`;
  4. report each call: an empty result means nothing is at or below the threshold,
     and not being a gap does not mean "as often as expected";
  5. give count, expected count, threshold and the conference-item rule; take
     denominators from `count_by` on the same basis, never as a sum of sub-area counts;
  6. check `coverage_report` and `search_publications` before calling a gap real.

  The prompt's example uses a different question (data types in Just Economies) from
  the test question, so the evaluation does not just check that the example is repeated.
- **`find_gaps` docstring** (tool description only, no signature change): compare an
  area by making it the row, not a filter; an empty list means no gap.
- **First test question** in `tests/questions.yaml` (`gaps-sg-methods`, #19). It
  requires `find_gaps` and a mention of geospatial or biophysical methods, the two
  sub-area gaps.
- **Two argument repairs in `run_tool`**, found in the same evaluation, for all tools:
  - qwen3.8 sends `filters` as a string holding a JSON object. Such a string is parsed
    when the parameter does not take a string anyway. Before this, every filtered call
    failed and qwen used up its budget.
  - A misspelt field gets a suggestion: `filters.impact_area: Extra inputs are not
    permitted (did you mean 'impact_areas'?)` (difflib, cutoff 0.75).
- **Wide brackets are normalised.** gpt-oss wrote `【BORIS:find_gaps, …】`, which slipped
  past the citation check of decision 0035. Full-width and lenticular brackets around a
  `BORIS:` group become `[]` before references are checked.

## Consequences
- `chatcde evaluate questions --id gaps-sg-methods --models both --runs 3`, 2026-11-11:
  - before the argument repairs: gpt-oss-120b 1 of 3, qwen3.8-27b 0 of 3;
  - after: gpt-oss-120b 2 of 3 (14.8 s per answer), qwen3.8-27b 3 of 3 (140 s);
  - qwen follows the steps every time. gpt-oss sometimes stops after the group-level
    call, and it still filters by the area when it asks for sub-areas.
- The prompt is longer (six sub-steps), which costs tokens on every question.
- Revisit:
  - whether qwen3.8-27b should be the agent model for gap questions, or `chatcde ask`
    should hand gap questions to the fixed `chatcde gaps` chain;
  - `find_gaps` returning its denominators (a contract change; decision 0035 notes it);
  - more gap questions in #19.
