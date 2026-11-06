# 0009: Downloading files from BORIS: one main PDF per record, a manifest per record

- Status: accepted
- Date: 2026-11-06

## Context
Issue #8 adds `chatcde harvest files`: each record's file from the BORIS `ORIGINAL`
bundle goes to `data/raw/`, and one `ManifestEntry` per record goes to the `downloads`
table. Parsing (#10) reads these files. CLAUDE.md also asks whether files marked
"restricted" in BORIS can be downloaded on the UniBe VPN. The answer decides how the
full edition (decision 0004) can be built.

Facts checked on BORIS (2026-11-06):
- `/core/items/{uuid}/bundles?embed=bitstreams` returns the bundles with their files
  in one request, so each record needs 2 requests (bundles, content), not 3.
- Each bitstream carries an MD5 `checkSum` and its `sizeBytes`.
- A restricted file's `/content` answers HTTP 401 with a JSON body. Off the network,
  BORIS sends its HTML access page with HTTP 200, as it does for the API.

## Decision
- **One file per record: the main PDF**, the largest bitstream whose name ends in
  `.pdf` (the same rule as the fixtures script). The schema has one `ManifestEntry`
  per record. Supplements, slides or data files in the same bundle are ignored.
  An `ORIGINAL` bundle without a PDF counts as `no_file`, with the file names in
  `error`, so other formats can be counted later.
- **Every record is tried, whatever its `access` value.** This answers the open
  question and catches records whose access field is wrong.
- **Status rules.** 401/403 → `restricted`. An HTML reply stops the run
  (`NotOnVPNError`, exit code 2) and keeps the entries written so far. An MD5
  mismatch, any other 4xx, or retries used up on 5xx or timeouts → `error`.
- **Storage.** The file goes to `data.files_dir/<record_id>.pdf` (default
  `data/raw/files`), written to a `.part` file first and then renamed. The original
  file name stays in `file_name`. No file is kept for a record that is not
  `downloaded`.
- **Resuming.** Each entry is committed as soon as it is known. A rerun skips
  `downloaded` (if the file is still there with the recorded size), `restricted` and
  `no_file`, and retries `error`. `--force` tries every record again; `--limit N`
  is for trial runs. Manifest rows of records that left the catalogue are dropped.
- **`records.has_fulltext`** is set from the manifest at the end of each run
  (`status = 'downloaded'`). The merge rule of decision 0008 keeps it across
  re-harvests.
- **Provenance.** `ManifestEntry` has no code version, so the run summary
  (`data/raw/boris/files_summary.json`) records the code version, counts by status
  and the table of BORIS access × download status.

## Results (full run, 2026-11-06)
`chatcde harvest files`: 3,003 records, about 6,000 requests, 37 minutes, no errors.

| BORIS access | downloaded | no_file | restricted | total |
|---|---|---|---|---|
| open | 1,542 | 13 | 18 | 1,573 |
| metadata_only | 0 | 899 | 0 | 899 |
| restricted | 0 | 1 | 530 | 531 |
| **total** | **1,542** | **913** | **548** | **3,003** |

- **Files marked restricted cannot be downloaded on the VPN**: 0 of 530 (all
  HTTP 401). The VPN opens the API, not the files. The full edition therefore needs
  another source for these texts (University Library, or `--top-up` with the
  user's own login, decision 0004).
- 18 records marked *open* refuse their file too (401); the item's access field and
  the file's own policy disagree. The status follows what BORIS actually serves.
- Of the 913 `no_file` records, 14 have files that are not PDFs (zip, pptx, mp3,
  html); the rest have an empty `ORIGINAL` bundle. Every `metadata_only` record
  has no file.
- The first run missed 6 PDFs: 5 publisher downloads named
  `…-main.pdf__tid=…&acdnat=…` and one file named `pdf`. The name rule now accepts
  these (`is_pdf_name`); their manifest rows were deleted and the stage run again.
  Five turned out to be restricted, one downloaded.
- 1,542 files, 6.3 GB (mean 4.1 MB, median 1.2 MB, largest 438 MB).
  `records.has_fulltext` is true for 1,542 records. The 30 fixture records have the
  same status and sha256 as `tests/fixtures/downloads.jsonl`.

## Consequences
- Full text is available for 51% of the records (1,542 of 3,003), all of them
  open access. The other half rely on abstracts and metadata (OpenAlex, #9), so
  any count over full text must say which records it covers.
- The files take 6.3 GB on disk; parsing and OCR read them from `data/raw/files/`.
- Scanned PDFs are downloaded like any other. Whether they hold a text layer is
  checked by the parse stage, which sends them to OCR if not.
- Later: download the other files in a bundle (supplements, non-PDF main files) if
  `no_file` with files turns out to matter; refresh files whose BORIS checksum
  changed (today only `--force` does that).
