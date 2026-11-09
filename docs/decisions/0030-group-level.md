# 0030: Count and find gaps by vocabulary group by default

- Status: accepted
- Date: 2026-11-09

## Context
Vocabulary v0.3 (#55, decision 0026) sorts the terms of method, data_type, setting,
system and actors into groups with their own codes, such as
`MET-G-participatory-and-transdisciplinary`. It ties each additional_sub_area term to
an impact area (0027).

At term level, gap analysis says little. 60 methods × 16 sub-areas gives 960 cells for
about 3,000 records, so most cells hold 0–2 records. 8 method groups × 16 sub-areas
gives 128 cells, where a low count means something.

#57 asked three questions about the shared formats (tool signatures, `VocabTerm`,
`Filters`); #58 implements the answers.

## Decision
- **`level` on `count_by` and `find_gaps`, default `"group"` for both.** The agent
  starts with the overview and drills down to terms with `level="term"` when a
  question names a single method or actor. The default is the same for both tools, so
  a count and a gap analysis of the same dimensions always match. The system prompt
  (`agent_system.v2`) says when to use which.
- **One `level`, not one per axis.** It applies to every dimension that has groups.
  The others (sub_area, scale, justice_lens, country, SDG, …) are always counted by
  term. The result says what was used: `CountMatrix.row_level` and `col_level`.
  Mixed questions, such as single methods within one system group, use a group code
  in the filters. One parameter fewer is one fewer for a mid-sized local model to get
  wrong.
- **No `list_groups` tool.** `list_vocabulary(dimension, level="term")` lists the
  terms, each with its `VocabTerm.group`. `level="group"` lists the groups with the
  records tagged with any of their terms. Every extra tool costs tokens in every
  request and is one more choice for the model.
- **Groups are read from the vocabulary when a query runs** (`group_map` in
  `tools/vocabulary.py`), not stored in the tags. Tags keep term codes (#49), so a
  regrouping in a later vocabulary version needs no re-tagging.
  - A dimension has groups when its curated terms carry `group`, or `impact_area`
    for additional_sub_area.
  - In v0.3 these are exactly the six named in #57; nothing is hard-coded.
- **A record counts once per group,** however many of the group's terms it has. Only
  tags at or above `min_confidence` count, as before. A term without a group
  (`other`, or a code not in the vocabulary) is counted under its own code, so nothing
  disappears from the denominators.
- **Group codes in filters** stand for all their terms. `Filters.methods =
  ["MET-G-biophysical"]` is expanded by `ToolContext.expand` before the search, the
  counts and `coverage_report` use the filters. The vocabulary is only read when a
  tag filter is set.
- **`chatcde gaps` (0024):**
  - it gets `--level` (default `group`);
  - each group cell carries its row and column terms with record counts (from one
    term-level count on the same filters), shown to the model and the reader;
  - the nearby records' tags are shown as group codes;
  - the prompt is now `gaps.v2`.
- **CLI:** `chatcde count … --level group|term`, `chatcde vocab -d DIM` with a Group
  column, and `--level group` to list the groups.

## Consequences
- `count_by` without `level` now returns groups, not terms. A caller that wants terms
  must ask for them. The CLI and the docstrings say so.
- Expected counts, `min_expected` and `max_ratio` apply to group cells unchanged.
  Fewer cells reach `min_expected` at term level, as intended.
- Revisit:
  - group level for sub_area (its impact area), if gaps across impact areas are
    wanted;
  - whether `count_by`'s default should go back to `term` if the agent misses
    term-level answers in `chatcde evaluate questions` (0025).
