# 0005: Add "transformative aspiration" (collaborate, challenge, disrupt)

- Status: accepted
- Date: 2026-10-27

## Context
CDE aims at sustainability transformations; the schema records research mode
(transformative vs transformation research) and pathway to impact, but not the
stance the work takes towards existing systems. Westerink et al. (2026) describe
three transformative pathways for networks: collaborate, challenge and disrupt.

## Decision
Add dimension 17, `transformative_aspiration`, with terms TA1 Collaborate,
TA2 Challenge, TA3 Disrupt and TA0 None explicit. Several values may apply. Tag
only when the text makes the stance clear.

## Consequences
- The framework was written for social networks working towards a nature-positive
  society; applying it to research publications is our adaptation, and should be
  tested on the reference set.
- It overlaps with research mode (M1 transformative research) and pathway P3
  (institutional work). Expect correlation; report it in `chatcde evaluate`.
- Expected distribution: mostly Collaborate or None explicit; Disrupt is likely rare.
  A low Disrupt count is a finding to interpret, not necessarily a gap.

## Source
Westerink, J., Nel, J., van Dam, R., et al. (2026). Transformative pathways for
social networks to navigate towards a nature-positive society: Collaborate,
challenge, and disrupt. Current Opinion in Environmental Sustainability, 79,
101617. https://doi.org/10.1016/j.cosust.2026.101617