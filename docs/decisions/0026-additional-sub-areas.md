# 0026: Additional sub-areas beyond the strategy

- Status: accepted (2026-11-08). The shared-format change (`Dimension.ADDITIONAL_SUB_AREA`)
  was proposed in #49 and is made in PR #55, where Track B reviews it.
- Date: 2026-11-08

## Context
Open extraction (0019) gave 86 theme candidates, 43 of them seen in at least 5 of the
250 sampled records. When asked where each belongs, the model found no strategy
sub-area for 61 of them. Examples: food security (14 records), climate change (14),
poverty reduction (14), deforestation (15), water resources (10), soil erosion (10).

The 16 strategy sub-areas (2025–2028) are fixed. A topic outside them has nowhere to be
recorded, yet such topics are exactly the gaps the assistant should find (use case 1)
and part of the frontier comparison with the wider field (use case 2).

## Decision
- **A new dimension, `additional_sub_area`**, labelled "Additional sub-areas (beyond
  the strategy)". It holds topics CDE works on that no strategy sub-area represents. It
  replaces the idea of a general `theme` dimension, because its name and scope state
  how it relates to the strategy.
- **Whether a topic is additional is decided once, at vocabulary level,** by the
  curator in the workbook (0025). The tagger only applies the closed list, and tags
  `sub_area` and `additional_sub_area` independently, so a record can carry both.
- **Boundary rule:** an additional sub-area must not overlap a strategy sub-area's
  scope. A candidate that partly overlaps, such as "Sustainable land management" and
  SL-a, is either merged into the sub-area (its labels become the sub-area's
  `includes`, and its scope note may be sharpened) or kept with a definition that draws
  the line. `vocab apply` refuses a term that repeats a sub-area label.
- **Every term has an `impact_area`.** It is a field on the term, not a dimension.
  Values:
  - JE / SL / SG / TE: the topic belongs to that impact area, but none of its sub-areas
    covers it (e.g. soil erosion under SL).
  - `cross_cutting`: the topic is relevant to several impact areas (e.g. climate
    change).
  - `outside`: the topic is in no impact area (e.g. mobility).
- **No "additional impact area" dimension.** Only a handful of candidates look truly
  outside, and a dimension for so few terms would add tagging cost and error.
  - `outside` terms may share a group, e.g. "Water and cryosphere" or "Mobility and
    migration".
  - These groups are candidate impact areas, which a later strategy review can promote.
- **A record's impact areas are derived in code** from the parents of its `sub_area`
  and `additional_sub_area` tags (SL-a → SL), not tagged separately. This keeps them
  consistent with the sub-areas. It changes the plan for #15, and is proposed in #49.
- **The list is kept narrow, about 25–35 terms.**
  - *Outcome in v0.3:* the curator accepted 77 terms (SL 26, cross_cutting 21, SG 11,
    JE 9, TE 5, outside 5), well above the aim. They are kept for the trial in #16, and
    narrowed if precision is low (see Revisit). Sixteen of them carry `related` links
    to a sub-area or term (0025), and a few overlap a sub-area's scope (e.g.
    Sustainability standards and certification and SG-b); the boundary rule is to be
    re-checked in v0.4.

## Consequences
- **Alignment can be measured, counted in code with denominators:** the share of
  records whose topics fall only within strategy sub-areas, only within additional
  sub-areas, in both, or in neither, plus the share of `cross_cutting` and `outside`
  terms, all per year.
  - Before 2025, this reads as how well the strategy reflects earlier work.
  - From 2025 on, it reads as how well work follows the strategy.
  - Decision 0016 (current categories for all years) makes the years comparable.
- The total goes from 17 to 18 dimensions (CLAUDE.md, planning.md, schema_draft.md).
- Topics are the hardest dimension to tag consistently. Expect lower precision than
  for methods or settings.
- Revisit:
  - **Trial condition:** if `additional_sub_area` precision in #16 is well below the
    threshold, merge its terms into the sub-area scope notes or drop the dimension.
  - Promote an `outside` group to a candidate impact area if it grows large in the
    full corpus.
