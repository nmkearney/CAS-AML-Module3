# 0015: Coverage report: text status, breakdowns, edition, ownership

- Status: accepted
- Date: 2026-11-07

## Context
Issue #11 asks for `chatcde coverage`: full-text, abstract-only and metadata-only
shares by year, type, language and access rights. The agent needs the same numbers
from the `coverage_report` tool (#24) before it claims that something is rare,
because a topic can look rare only because few such records have readable text
(planning.md, principle 4). The tool signature is in `tools/core.py`, and
`CoverageReport` is in `schemas.py`.

## Decision
- **Track A writes `coverage_report()` and `chatcde coverage`; Track B reuses them.**
  Proposed on #11 and #24, pending Simon's agreement. Coverage depends on data
  Track A produces (access rights, downloads, pages, abstracts, tags, the bundle's
  edition), and one implementation keeps the CLI and the agent from disagreeing.
  The logic is in `chatcde.tools.coverage`, as functions that take an open
  connection. `core.coverage_report(filters)` loads the config and opens the
  catalogue read-only. If Track B settles another way for core tools to get their
  connection (#23, #25), the wrapper follows it.
- **One text status per record:**
  - `fulltext`: at least one row in `pages` with non-blank text;
  - `abstract_only`: an abstract from BORIS or OpenAlex;
  - `metadata_only`: neither.
  The three add up to `total_records`. Parsed pages are the measure because they are
  what the assistant can read: a downloaded scan without OCR does not count. **Before
  any pages exist**, `records.has_fulltext` (a downloaded PDF) stands in, and the CLI
  says so (`fulltext_basis`).
- **Filters** are resolved by Track B's `store.filters.filter_record_ids`, so coverage
  restricts exactly as search and counts do (0013).
- **Breakdowns:**
  - `by_year`: `unknown` when there is no year, listed last;
  - `by_type`;
  - `by_language`: first listed language only, so every breakdown adds up to the
    total; `none` when there is none;
  - `by_access`.
  `CoverageReport` gains `by_type` and `by_access` (#43). Both are plain counts with
  empty defaults, so nothing that exists breaks. The status split per breakdown is in
  `breakdown()` and the CLI, not in the schema.
- **`low_confidence_tags`** counts tag rows on the matching records with
  `confidence < filters.min_confidence` (default 0.5). It is 0 before tagging.
- **Edition:** `manifest.json` in `data.dir`, written by a bundle (`chatcde fetch-data`,
  #18), gives the edition. Without a manifest the data was built locally and counts as
  `full`.

## Consequences
- First run on the real catalogue (2026-11-07, 3,003 records): 1,542 full text (51%),
  606 abstract only (20%), 855 metadata only (28%). Restricted and metadata-only
  records never have full text. Of the 1,573 open records, 98% do.
- `#18` must write `manifest.json` into `data.dir` (or this lookup must change with it).
- Revisit:
  - whether `by_language` should count every listed language;
  - whether the agent needs the status split per breakdown in `CoverageReport`;
  - a `with_tags` count once tagging (#15) runs.
