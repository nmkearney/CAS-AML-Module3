# 0024: `chatcde gaps`: a fixed chain in code, one structured judgement by the LLM

- Status: accepted
- Date: 2026-11-08

## Context
Issue #27 asks for the gap workflow with a "fixed tool chain and output format" and a
versioned prompt. The planning doc gives the chain: `list_vocabulary` → `count_by` →
`find_gaps` → `search_publications` on thin cells → `coverage_report`. The chat adds a
judgement: is a gap real, a tagging artefact or a deliberate choice? Counts must come
from code, with denominators (CLAUDE.md). Conference items are left out unless
included (0018).

## Decision
- **The chain runs in code, not in the agent loop.** A mid-sized local model that
  chooses its own tools may skip steps or call them in another order. Here
  `chatcde.workflows.gaps.run_gaps` runs the steps itself, in this order:
  1. `gap_report` (0021) for the two dimensions within the scope;
  2. for each of the first `gaps.max_cells` (8) gap cells, `search_publications`
     within the same scope for the two terms' labels. The `gaps.passages_per_cell`
     (3) records found are shown with their own tags on both dimensions;
  3. caveats computed by code: untagged records, left-out types, the
     `min_expected` rule, cells not shown, failed searches, and the data note
     (edition and full-text coverage, 0023).
- **One LLM call judges the cells.** The `agent` role model gets the prompt
  `gaps.v1` and the evidence as JSON. It replies following the `GapsJudgement` JSON
  schema (`response_format`, strict; `gaps.structured_output` can turn this off). For
  each cell the reply gives a verdict (`real_gap`, `tagging_artefact`,
  `deliberate_choice` or `unclear`), a short reason and the evidence record IDs, plus
  a summary and extra caveats.
- **The model never supplies numbers.** The table's count, expected and ratio come
  from step 1. The prompt forbids the model to state counts. Evidence IDs that are not
  in that cell's records or nearby records are dropped and listed in red. Cells the
  model leaves out become `unclear`.
- **Failure keeps the counts.** If the search fails, that is noted on the cell and in
  the caveats. If the judgement fails (endpoint, invalid JSON, cut off), the reason is
  shown with the table. `--no-llm` stops after step 3, for offline checks.
- **Scope = the shared filter options** (`--unit`, `--sub-area`, `--region`,
  `--country`, `--year-from`, ...) rather than a single `--scope` string as sketched in
  the planning doc. They are the same filters as `count` and `search`, so a gap can be
  rechecked with `chatcde count ROW COL --gaps` and the same options. `--dims` takes
  `row,col` (default `gaps.dims`, `sub_area,method`).
- **Output:** a header with the scope and denominators, then the evidence table
  (count, expected, ratio, verdict, source numbers), the summary, one line per cell
  with the nearby records' tags, numbered sources (DOI and BORIS links), caveats (the
  model's marked "(model)"), the data note and a footer with model, prompt and
  vocabulary versions. `--json` prints the whole `GapsResult`, and `--log FILE`
  appends it as a JSON line.
- `coverage_report` (Track A, #45) is not merged yet. Until then step 3 uses the
  data note's counts, which follow the same rule.

## Consequences
- The same evidence package can be judged by both agent models (`--alternative`) and
  compared in #28.
- The search query is the two labels joined ("Land governance Interviews"). This is
  crude, but cheap and the same every run.
- Not yet tried against GPUStack: check that gpt-oss-120b honours the strict schema
  and that 8 cells fit `gaps.max_tokens` (6,144).
- Revisit:
  - add the `coverage_report` call per cell once #45 is merged, so each cell gets
    its own full-text share;
  - query terms from the vocabulary definitions;
  - the verdict list, after the first runs on real tags.
