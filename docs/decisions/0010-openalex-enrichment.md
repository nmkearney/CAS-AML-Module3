# 0010: OpenAlex enrichment: which DOIs, which abstracts, where OA links go

- Status: accepted
- Date: 2026-11-07

## Context
Issue #9 adds `chatcde harvest openalex`. It fills abstracts that BORIS lacks and
notes open-access (OA) PDF links. After #7 and #8, 1,665 of the 3,003 records have
no abstract, and 548 files could not be downloaded because they are restricted.

Facts checked on 2026-11-07:
- **`Record.doi` is BORIS's own DOI.** It comes from `dc.identifier.doi`, and
  every one of the 2,111 values has a BORIS DataCite prefix (`10.7892` for 1,341,
  `10.48350` for 478, `10.48620` for 292). The publisher's DOI is a separate field,
  `dc.identifier.publisherDOI`. It is set on 981 records and is not in the shared
  schema.
- **OpenAlex replaced the mailto "polite pool" with API keys** and a daily budget:
  - without a key, $0.10/day shared per IP, 10 requests/s;
  - with a free key, $1/day;
  - a filter request costs $0.0001 and can match up to 100 DOIs (`doi:a|b|…`).
- OpenAlex gives abstracts only as an inverted index. Some are text-mined from
  full text, including from BORIS PDFs.

## Decision
- **Look up both DOIs of every record**, about 3,087 distinct DOIs in 31 requests:
  - the publisher DOI, read from the saved BORIS search pages (no schema change);
  - the BORIS DOI.

  The publisher DOI's work is preferred for the abstract. All records are looked up,
  not just those missing an abstract, so that OA links are noted for every record.
- **No API key is needed.** A full run costs about $0.003. `openalex.api_key_env`
  names an optional variable, sent as `Authorization: Bearer`. `mailto` is sent only
  if set.
- **BORIS always wins.** An OpenAlex abstract is written only where `abstract IS NULL`,
  with `abstract_source = abstract_openalex`. Each apply first clears earlier OpenAlex
  abstracts, so the result depends only on the cache and the config.
  `harvest metadata` keeps them on rebuild (decision 0008).
- **Cleaning:** the inverted index is rebuilt in word order, whitespace is collapsed,
  and a leading "Abstract"/"Zusammenfassung" label is stripped (also the glued
  `ABSTRACTText` form). Abstracts under `min_abstract_words` (30) are rejected. They
  are mostly one-sentence teasers, correction notices and fragments.
- **OA links go to `data/raw/openalex/oa_links.csv`**, not the schema:
  - one row per record, choosing the best OA location (PDF URL, else landing page);
  - a link outside BORIS is preferred over one that points back to BORIS;
  - `host_is_boris` marks links on `openalex.boris_hosts` or with a BORIS DOI
    prefix (`openalex.boris_doi_prefixes`);
  - `boris_download_status` is joined from `downloads`, so the later `--top-up`
    (decision 0004) can pick links that add something.
- **Two phases**, as in `harvest metadata`:
  - each reply is saved as `lookups/batch-NNNN.json`;
  - `openalex_state.json` lists the DOIs already looked up, so a rerun makes no
    requests;
  - `--offline` re-applies from the cache, and `--force` starts over.

  `openalex_summary.json` is the provenance record.

## First run (2026-11-07)

| | Count |
|---|---|
| Records with any DOI | 2,201 / 3,003 (981 with a publisher DOI) |
| DOIs known to OpenAlex | 1,860 / 3,087 |
| Missing abstracts filled | **210 / 1,665** (136 via publisher DOI, 74 via BORIS DOI) |
| … by type | 125 article, 24 report, 24 conference_item, 16 book_section, 13 book, 8 other |
| Work found, but no abstract in OpenAlex | 606 |
| Rejected as too short | 21 |
| No work found | 226 |
| No DOI at all | 602 |
| Still without an abstract | 1,455 / 3,003 |
| OA links | 1,698 (775 outside BORIS) |
| Outside-BORIS links where BORIS gave no file | 183 (154 restricted, 29 no_file; 113 with a direct PDF URL) |

## Consequences
- Abstract coverage rises from 1,338 to 1,548 of 3,003 records (45% → 52%). Older
  grey literature (reports, conference items, book sections) gains least: OpenAlex
  has no abstract for most of it.
- Some of the 74 abstracts found via a BORIS DOI were text-mined from the BORIS PDF.
  Examples are slide bullets and missing spaces after full stops. They describe the
  work, but they are less clean than an author's abstract. `abstract_source` does not
  separate them; the CSV and cache do.
- 183 restricted or missing BORIS files have a legal OA copy elsewhere, mostly a
  publisher PDF. `--top-up` or a later `harvest files` option could fetch these for
  the open edition.
- Later:
  - propose (issue labelled `schemas`) a `publisher_doi` field on `Record`, since
    "traceable to DOIs" in CLAUDE.md is better served by the publisher DOI;
  - try title matching for the 602 records with no DOI;
  - re-run monthly; new DOIs are looked up incrementally.
