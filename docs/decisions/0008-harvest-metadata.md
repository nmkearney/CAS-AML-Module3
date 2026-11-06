# 0008: Harvesting BORIS metadata: which records count as CDE's, caching, merging

- Status: accepted
- Date: 2026-11-06

## Context
Issue #7 ports `scripts/boris_cde_inventory.py` into `chatcde harvest metadata`.
The search for `"Centre for Development and Environment"` returns 8,602 items
(2026-11-06), most of which only mention CDE somewhere. A rule must decide which
items become `Record`s, and the counts behind every "gap" depend on that rule. The
stage must also be resumable and must not undo the work of later stages.

First live run, publications by rule:

| Rule | Publications |
|---|---|
| author or editor affiliation names CDE | 2,343 |
| + org unit (`dc.description.sponsorship`) | 2,368 |
| + CDE as publisher (`dc.publisher`) | 2,530 |
| + `dc.relation.organization` | 3,054 |

`dc.relation.organization` is present only on records migrated from the old
EPrints BORIS (`unibe.eprints.legacyId`). It holds the divisions a record was
credited to there. On 524 publications it is the *only* place CDE appears, because
the migration left the author affiliations empty (placeholders). Of these,
273 are sponsored by the NCCR North-South Management Centre (run by CDE), 106 by
Integrative Geography / GIUB (CDE's former home, mostly 2004–2011) and 145 by other
institutes, e.g. plant sciences.

## Decision
- **Explicit CDE records are kept**: CDE appears in an author or editor affiliation
  (`oairecerif.{author,editor}.affiliation{,2,3,4}`), the org unit
  (`dc.description.sponsorship`) or the publisher.
- **Org-linked records** (CDE only in `dc.relation.organization`) are kept if
  they are confirmed in one of two ways:
  - *nccr*: sponsored by the NCCR North-South Management Centre (developer's call:
    the NCCR belongs to CDE's history);
  - *person*: an author or editor is CDE-affiliated on an explicit record of the
    same harvest. Names are compared with whitespace collapsed and case folded,
    because legacy authors carry no person entity.
  Result (2026-11-06): 2,529 explicit + 273 nccr + 201 person = **3,003
  publications**. Left out: 50 unconfirmed (49 from other institutes, 1 geography).
  The summary reports these counts as `by_cde_link`.
- **Publications only.** Entity types other than `Publication` (Audiovisual,
  Project, Product, Software) and withdrawn items are left out of `records`. They
  stay in the raw cache for later tools (`get_project`, `find_datasets`).
- **ORCIDs by person, not position.** `cris.virtual.orcid[j]` belongs to the person
  entity `cris.virtualsource.orcid[j]` and is matched to each author's `authority`.
  Decision 0007 assumed alignment by position, which gave 65 of the 135 real fixture
  authors a wrong or missing ORCID. `tests/fixtures/authors.jsonl` was rebuilt with
  this fix. Across the harvest, only 7 ORCIDs map to more than one surname, all of
  them spelling variants.
- **Two phases.** *Fetch* saves each search page as returned to
  `data/raw/boris/search/page-NNNN.json`, in a fixed sort order
  (`dc.date.accessioned,ASC`), with `harvest_state.json` for resuming. *Build*
  (`--offline`) rebuilds `records` and `authors` from the cache in one transaction,
  so mapping fixes need no new requests. `--force` fetches every page again.
- **Merge rule.** A rebuild keeps `has_fulltext` from the existing catalogue (file
  download, #8) and keeps an OpenAlex abstract (#9) when BORIS has none. Records that
  no longer qualify are removed and counted.
- **Provenance.** `Record` has no provenance field, so the code version, query and
  counts are written to `data/raw/boris/harvest_summary.json` and the log.

## Consequences
- The catalogue is about 20% larger than the inventory's 2,507, mostly from older
  (2004–2011) NCCR and geography work. Any gap or trend over time must take this
  into account. The most visible effect is on records from before 2012.
- `cde_units` is set on only 549 records. Older work has no unit, as expected.
- Matching people by name can miss name variants (Zähringer and Zaehringer), which
  would drop an org-linked record, and can wrongly match a namesake. Revisit if the
  50 unconfirmed records turn out to matter.
- Later: incremental re-harvest by `lastModified`; a `cde_link` field on `Record`
  (needs a `schemas` change) so tools can filter out org-linked records; a check
  of the 145 other-institute records with CDE staff.
