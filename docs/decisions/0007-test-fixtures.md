# 0007: Test fixtures: selection, licences, tag format and fixture DB layout

- Status: accepted
- Date: 2026-11-06

## Context
Issue #3 needs a small, committed stand-in catalogue so Track B can work before
the real pipeline exists. Committed data must be redistributable, and the
fixtures should cover the variety of the real corpus. Building them meant several
choices that the real pipeline will meet again.

## Decision
- **Licence rule.** A real record is used only if the `dc.rights.uri` of its main
  PDF in the `ORIGINAL` bundle is CC BY, BY-SA, BY-NC, BY-NC-SA or CC0. BY-NC is
  accepted because the repository is non-commercial. ND is rejected because we
  store extracted text. BORIS's "free to read" statement
  (`https://boris-portal.unibe.ch/info/about`) and a missing licence are rejected.
- **Selection.** Stratified by first CDE unit, decade, language and type: first
  cover every value (rarest first), then fill weighted by √(pool share). The
  candidate list is ranked with reserves, and the fetch takes reserves in order when
  a record fails the licence, text-layer or minimum-text (1,500 characters on pages
  1–3) check. Records excluded in review are listed in `tests/fixtures/excluded.csv`.
- **Pages.** Pages 1–3 of each PDF are kept, plus the first 3 body pages
  (detected by heuristic, overridable in `tests/fixtures/body_pages.csv`). Front
  matter stays because the real pipeline sees it too; the body pages make the tags
  rest on content rather than covers and contents lists. Page numbers are the real
  PDF page numbers, so a record's pages need not be contiguous.
- **Tag terms.** `Tag.term` is the vocabulary code where the YAML has one (`SL-d`,
  `O2`, `TA1`, `M1`, `P3`). For dimensions without codes it is the exact YAML label
  (`Case study`, `Mountains`, `Smallholders and land users`). Countries are ISO
  alpha-3 or `global_or_multi_country`. SDGs are the strings `"1"`–`"17"`. Regions
  are a CDE partner region name or a UN M49 region or sub-region name. `output_type`
  holds the audience; the BORIS type is already in `Record.pub_type`.
- **Evidence.** `Tag.evidence` is a verbatim quote from the text named by
  `Tag.source`, matched after NFKC normalisation, removal of soft hyphens and
  whitespace collapsing. Derived tags (region from country, `None explicit`) may
  have no quote.
- **Review record.** The `Tag` schema has no note or review fields, so notes and
  the hand check live in `tests/fixtures/tag_review.csv`, keyed by
  (record_id, dimension, term, role). The schema is unchanged.
- **Who drafted the tags.** Claude (claude-opus-5-5) drafted them in a coding
  session, not through the pipeline. This is reference data, not pipeline output.
  `provenance.model = "claude-opus-5-5 (session draft)"`. Every tag is then checked
  by hand.
- **Older work** is tagged with the 2025–2028 categories, keeping the BORIS units as
  recorded (the proposal in CLAUDE.md, applied here).
- **DuckDB layout.** One table per model, using the names in the `schemas.py`
  docstring. Columns come from the pydantic fields: str/enum → `VARCHAR`,
  int → `BIGINT`, float → `DOUBLE`, bool → `BOOLEAN`, list → `T[]`,
  dict → `MAP`, nested model (`Provenance`) → `STRUCT`, datetime → `TIMESTAMP`
  in UTC. `TIMESTAMPTZ` was avoided because reading it into Python needs `pytz`.
  Optional fields are nullable; all others are `NOT NULL`.
- **BORIS field mapping**, checked against live items on 2026-11-06:
  `#PLACEHOLDER_PARENT_METADATA_VALUE#` means empty. `dc.contributor.author` and
  `oairecerif.author.affiliation{,2,3,4}` are aligned by position.
  (Corrected in 0008: `cris.virtual.orcid` is *not* aligned with the authors; it
  follows `cris.virtualsource.orcid`, the person entity of each author.) Access is in `datacite.rights` (`open.access`, `restricted`,
  `metadata.only`, `embargoed.access`). Keywords come from `dc.subject` and
  `dc.subject.keywords`; `dc.subject.ddc` (Dewey classes) is left out. The container
  is the first of `dc.relation.ispartof`, `…ispartofbook`, `…ispartofseries` and
  `…magazine`. The handle is the item's `handle`, and the BORIS URL is
  `dc.identifier.uri`.

## Consequences
- Only ~40% of "open access" records are openly licensed, which skews the
  fixtures: no records before 2000, none coded French or Spanish, one Sustainable
  Land Systems record. The open data edition (decision 0004) will face the same
  skew in full text; it should be reported by `coverage_report`.
- Track B can rely on the term format above. `list_vocabulary` and the tagging
  prompt should use the same rule, or this decision should be revised.
- The DuckDB layout is a proposal for `store/`. If Track B changes it, update
  `tests/fixtures/build_fixture_db.py` with it.
- Revisit: whether to add explicit codes to the label-only dimensions in the
  vocabulary, which would make terms stable when labels are reworded.
