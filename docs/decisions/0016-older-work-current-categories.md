# 0016: Older work is tagged with the 2025–2028 strategy categories

- Status: accepted
- Date: 2026-11-07

## Context
Dimensions 1–5 of the schema (impact area, sub-area, objective, pathway, research
mode) come from CDE's Strategy 2025–2028. Almost none of the corpus was written under
that strategy. Of the 3,003 records in the catalogue (2026-11-07):

| Period | Records | With a BORIS unit |
|---|---|---|
| before 2010 | 536 | 1 |
| 2010–2024 | 2,199 | 328 |
| 2025 onward | 268 | 220 |

BORIS units appear from 2013–2016 (one JE record dates from 1989). Earlier strategies
had other research areas (land resources, socio-economic transitions, education for
sustainable development), but there is no reliable record of which publication
belonged to which area. The other option was a crosswalk from the old areas to the new
ones, kept as a second vocabulary.

## Decision
- **Every record, whatever its year, is tagged with the 2025–2028 categories** for
  dimensions 1–5. The tags describe what a publication is about in today's terms, not
  which programme produced it.
- **`records.cde_units` stays exactly as BORIS records it.** Tags never fill in or
  correct a unit. Units and tags are separate fields, so the history stays visible.
- **No crosswalk** to earlier strategy areas for the prototype.
- **Gap and trend answers can be split by period** (before 2010, 2010–2024, 2025
  onward) with the existing `year_from` / `year_to` filters, so old work does not hide
  a gap in recent work, and the other way round. Fixed period bands go into
  `config/default.yaml` only when a tool needs them (#24, #27).

## Consequences
- One vocabulary covers the whole corpus. Counts across decades compare like with like.
- The tagging prompt (#15) must say that the categories apply retrospectively and that
  a publication need not use the strategy's wording to fit a category.
- The open-extraction sample (#13) is stratified by decade, so it shows whether the
  categories fit older work, and where they don't.
- BORIS units cannot serve as a check on impact-area tags before about 2016: only 549
  records have a unit, almost all from 2010 on.
- Pathways (P1–P5) are tagged only when explicit, so older work will rarely get one.
  That reflects the text, not a gap.
- Revisit: a crosswalk to earlier strategy areas, if CDE wants to ask how its own
  priorities shifted over time.
