# 0034: Filters cover every tagged dimension and reject what they don't know

- Status: accepted
- Date: 2026-11-11

## Context
`Filters` (schemas.py) had tag filters for five dimensions only: countries,
regions, sub-areas, methods and SDGs. Pydantic ignores unknown keys by
default. In a test run on 2026-11-11 the agent asked
`count_by(row="method", filters={"impact_areas": ["SG"]})`. The filter was
dropped without a word, and the answer on "Sustainable Governance" used counts
over all 3,003 records. `coverage_report` dropped it in the same way. With
the filter, SG has 1,275 records (779 with full text).

A second silent failure was close by. Tag filters match by code, so a label
("Sustainable Governance"), a misspelt code or a retired code matched no
record, and the result looked like a real zero. A wrong zero is the most
damaging error for gap questions.

## Decision
- **Every tagged dimension has a filter field**, named as the dimension's
  plural: `impact_areas`, `sub_areas`, `additional_sub_areas`, `objectives`,
  `pathways`, `research_modes`, `transformative_aspirations`, `methods`,
  `data_types`, `countries`, `regions`, `settings`, `scales`, `systems`,
  `actors`, `justice_lenses`, `sdgs`, `output_types`. `store.filters.TAG_FIELDS`
  maps each to its dimension; a test checks that the two cover all 18.
- **Unknown fields are rejected** (`extra="forbid"`). The tool definition then
  carries `additionalProperties: false`, and `run_tool` returns the validation
  error to the model, which can retry.
- **Unknown codes are rejected** in `ToolContext.expand`, which every tool calls
  before using filters. Known codes are the vocabulary's term and group codes
  plus `other`. For countries they are the ISO alpha-3 codes in `vocab/m49.csv`
  and the global flag. For regions they are the partner regions, the finest M49
  region names and the partner-country flag. Matching ignores case. The error
  names each bad value and suggests the code: for a label, the replacement of
  a retired code, or close matches (difflib). Short lists are printed in full.
  If `m49.csv` is missing, countries and regions are not checked (logged).
- **CLI:** `--tag dimension:CODE` (repeatable) on `search`, `count`, `coverage`
  and `gaps`, next to the existing options, through `cli_common.build_filters`.
  It avoids 13 new options per command.
- Actor roles (studied / co-producer / audience) are not filtered yet: the
  filter matches the actor code in any role.

## Consequences
- A misspelt or invented filter fails visibly instead of producing a plausible
  answer over the wrong records.
- Changes the shared contract: code or saved JSON that passes unknown keys to
  `Filters` now fails. No such use was found in the code or tests.
- Retired codes are refused even though old tags could in principle still hold
  them; after a vocabulary change, retag or migrate (see #60, #75).
- Revisit: a role filter for actors; whether the agent prompt should list the
  filter fields explicitly (the tool schema already does).
