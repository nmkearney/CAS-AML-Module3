# 0036: Count results reach the model whole or not at all

- Status: accepted
- Date: 2026-11-11

## Context
On 2026-11-11 the agent asked `count_by(row=sub_area, col=method,
filters={sub_areas: [SG-a..SG-d]}, level=group)`. The JSON was 16,114 characters, just
over `agent.max_tool_result_chars` (16,000), so the result was cut and a note was
added (decision 0022). The model ignored the note and reported the missing cells as
zeros ("MET-G-geospatial-and-land: 0 SG records"), but `chatcde count method` with the
same filters gives 129 (#81). For gap questions, a plausible zero is the worst error
there is.

Most of the size was `record_ids`, up to 20 per cell. The matrix also has more rows
than the question suggests: a sub-area filter keeps the SG records, but their other
sub-areas (SL-a, SL-b, …) are counted too, so it had 116 cells. On the real
catalogue the same call is 72,033 characters with 20 IDs per cell, 25,264 with 3 and
13,393 with an empty list in every cell.

## Decision
- **Counting tools send no record IDs by default.** For the tools in
  `agent.COUNT_TOOLS` (`count_by`, `find_gaps`, `coverage_report`, `trend`), the
  `record_ids` lists are cut to `agent.max_ids_per_count_cell` (default 0). At 0 the
  key is dropped altogether: each cell already carries its `count`, and counts are
  cited as queries, not records (decision 0035). Example records come from
  `search_publications`. Other tools keep `agent.max_ids_per_list` (20).
- **Count results are never cut.** If a count result is still longer than
  `agent.max_tool_result_chars`, the model gets an error instead, naming the number
  of cells and the size, and asking for fewer cells (`level='group'`, one dimension,
  narrower filters). The failed call is not listed among the answer's count queries.
- Other tools' results are still cut with a note, as before (decision 0022).
- The full result stays in the code: IDs in count results still count as returned by
  a tool when an answer cites them.

## Consequences
- The #81 call is now 8,783 characters and is sent whole. Group-level matrices
  over the sub-areas fit easily; term-level matrices of two grouped dimensions
  (e.g. sub_area x method, 568 cells, 39k characters) are refused, and the model has
  to start at group level and drill down, as #67 asks.
- The model can no longer pick a record ID from a count cell to cite. That was never
  the intended way to find examples.
- Revisit: a more compact layout for matrices (rows as objects of column counts)
  would roughly halve the size; and whether a filter on a dimension should also
  limit the rows of that dimension (only SG sub-areas when filtering by SG
  sub-areas).
