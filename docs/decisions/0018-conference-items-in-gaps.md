# 0018: Conference items are left out of gap counts by default

- Status: accepted (implementation by Track B, see Consequences)
- Date: 2026-11-07

## Context
Gap analysis (`count_by`, `find_gaps`, `chatcde gaps`) compares counts of tagged
records. Conference items are 672 of 3,003 records (22%), but they carry little
evidence: 145 have an abstract (22%), 278 have a downloaded file (41%), and 619 have no
container (no proceedings volume or series). Many are talks or posters on work that
was later published as an article or report. Counting them alongside other
publications counts popular themes twice, and their thin metadata makes them likely to
be untagged or tagged with low confidence. The options were to count them like any
record, to count them but report them separately, or to leave them out by default.

## Decision
- **Gap counts leave out `pub_type = conference_item` by default.** This covers
  `count_by` and `find_gaps` when used for gap analysis, and the `chatcde gaps`
  workflow. The user can include them (for example `--include-type conference_item`).
- **The output always says what was left out**: how many conference items matched the
  other filters and were excluded, so the denominator is explicit (planning.md,
  principle 3).
- Search, `get_publication`, `read_section`, `coverage_report` and expertise finding
  still cover conference items. A conference talk is still evidence that someone works
  on a topic.
- **Mechanism:** a config key, proposed as `gaps.exclude_types: [conference_item]` in
  `config/default.yaml`. The gap tools turn it into an explicit `Filters.pub_types`
  list (every other type), so the shared `Filters` schema does not change.

## Consequences
- Gap tables rest on publications with more text and fewer duplicates. The cost is
  that a theme presented mostly at conferences looks thinner than it is; the excluded
  count makes this visible.
- Track B implements this in #24 and #27; an issue links back to this decision. The
  config key's name and place are agreed there.
- When `count_by` is used for plain counts (not gaps), all types count unless the user
  filters.
- Revisit: match conference items to the later articles they became and drop only the
  duplicates; check whether other thin types (magazine articles, `other`) need the
  same treatment.
