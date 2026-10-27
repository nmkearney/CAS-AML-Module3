# 0004: Two data editions, full and open

- Status: accepted; full-edition location pending
- Date: 2026-10-27

## Context
Almost all ChatCDE users are UniBe employees, who can access the same documents
the developer harvests. Some full texts are restricted publisher versions that
should not be published openly.

## Decision
- The pipeline builds one index over everything harvested, then exports two editions:
  - **full**: all records with full text.
  - **open**: the same index filtered to open-access records for full text;
    metadata and abstracts for the rest. Same embeddings, no re-embedding.
- The full edition is hosted on CDE's server, reachable only from the UniBe
  network or VPN. The server enforces access; the client only chooses.
  Location: placeholder in `config/default.yaml` until confirmed.
- The open edition is hosted publicly (GitHub Releases or Zenodo).
- `chatcde fetch-data` probes the full-edition URL with a short timeout;
  if reachable it downloads full, otherwise open. `--edition full|open` overrides.
- Each bundle has a manifest: edition, version, build date, vocabulary and model
  versions, record counts, checksums. `chatcde info` and `coverage_report`
  state the edition and its full-text coverage.

## Consequences
- A full edition downloaded on the VPN keeps working off the VPN.
- To confirm with the University Library: copying restricted publisher texts to
  staff machines is acceptable.
- Fallback if not: users get the open edition and run
  `chatcde fetch-data --top-up` on the VPN to download the remaining PDFs from
  BORIS under their own access and index them locally.
- The harvest must confirm that "restricted" files are actually downloadable on
  the VPN (download manifest: downloaded / restricted / missing).
