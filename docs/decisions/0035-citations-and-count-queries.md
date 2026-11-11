# 0035: Answers cite only records; counts are listed as queries

- Status: accepted
- Date: 2026-11-11

## Context
On 2026-11-11 an answer about Sustainable Governance cited `[BORIS:count_by]` and
`[BORIS:coverage_report]`. The citation pattern accepts any word as an ID, so these
went through to the terminal as `[?]` with a red warning, and the numbers they were
meant to back had no visible source. The rule is that every answer is traceable to
BORIS IDs or DOIs, and every count to a catalogue query with its denominator.

## Decision
- **References that are not records are sent back once.** After the agent answers,
  every `[BORIS:…]` ID is looked up in the catalogue. If any is unknown, the model
  gets the answer back with a short note (`agent.CITATION_NOTE`) and rewrites it,
  without tools (`agent.citation_retry`, default on). Only the corrected answer stays
  in the conversation; `AgentResult.retried_ids` records what triggered the rewrite.
- **What is still unknown is removed** from the answer text (an emptied bracket goes
  with the space before it) and kept in `AgentResult.unknown_ids`, which the terminal
  shows as a warning and `chatcde evaluate questions` counts as a failure.
- **Counts are cited as queries, not records.** Every successful call of a counting
  tool (`count_by`, `find_gaps`, `coverage_report`, `trend`) becomes a `CountQuery`
  (tool, arguments, a one-line summary of the denominator) in `AgentResult.counts`.
  `chatcde ask` lists them under the sources, e.g.
  `count_by(row=method, impact_areas=[SG]): 1,275 records match, N of them have no
  method tag`. Identical calls are listed once.
- **Prompt `agent_system.v3`**: BORIS references are for records only; give each
  number in the text with its denominator; the count queries are listed
  automatically.
- Records that are in the catalogue but were never returned by a tool are still kept
  and flagged ("not returned by any tool"), as before: they are traceable, only not
  checked against this conversation's evidence.

## Consequences
- An answer never shows a reference that leads nowhere, and every number has its
  query beside it.
- The rewrite costs one extra request, only when the first answer is wrong.
- The Counts list shows every count query the agent ran, not only those whose
  numbers the answer uses: matching numbers in the text to queries is left open.
- `find_gaps` returns cells without a denominator, so its line gives only the
  number of thin cells; #67 may add one.
- Revisit: numbered count references (`[C1]`) that tie a sentence to its query.
