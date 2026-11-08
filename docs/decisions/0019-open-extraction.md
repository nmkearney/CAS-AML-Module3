# 0019: Open extraction on a stratified sample (`chatcde vocab extract`)

- Status: accepted
- Date: 2026-11-08

## Context
Issue #13 is step 2–3 of the vocabulary process (docs/planning.md). The draft vocabulary
(`vocab/draft_vocabulary.yaml`) comes from the 2025–2028 strategy and from our own
proposals, and no workshops will review it (0002). We need evidence of what CDE
publications actually contain before the developer curates the terms (#14) and the corpus
is tagged (#15).

Some constraints:
- Only about 550 of 3,003 records carry a CDE unit.
- About 16% are German, and only about 60 records predate 2000.
- About half the records have page text (open access), and half have an abstract.

## Decision
- **One command, three resumable steps:**
  `chatcde vocab extract --step sample|label|propose|all`.
  `--force` redoes the chosen step, and `--limit` makes a trial label run.
- **Sample: unit × decade × language, balanced with floors.**
  - The strata are:
    - unit: the first CDE unit as an impact-area code (via `boris_unit`), `services`, or
      `none`
    - decade: `<2000` pooled, then `2000s`, `2010s`, `2020s`, or `unknown`
    - language: `en`, `de`, or `other`
  - Allocation: each non-empty stratum gets `min(stratum_floor, size)` records (3). The
    remainder of `sample_size` (250) is shared in proportion to what is left in each
    stratum, by largest remainder. This oversamples units, German and older work
    compared with proportional allocation, so they are seen at all.
  - Within a stratum, records with page text come first.
  - Only records with an abstract or page text are eligible.
  - The draw is seeded (`seed: 13`) and kept in `vocab_sample` until the settings change,
    so labels stay valid if the catalogue grows.
- **Six label kinds in one call per record:** methods, themes, data types, settings,
  systems and actors (with a role of studied, co-producer or audience). Methods and
  themes are what the issue asks for. The other four are the dimensions whose seed lists
  we proposed ourselves rather than took from a CDE source, so they need the same check.
  Adding them costs only output tokens.
- **The seed vocabulary is not shown during labelling**, so the labels reflect the texts
  rather than echo our draft. It is shown only in the propose step, where matching
  against it is the point.
- **Labels are in English, with quotes in the original language.** One label language
  lets German and English work cluster together. The quote stays verbatim so it can be
  checked. The code sets `evidence_found` when the quote's letters and digits occur in
  the input, ignoring spaces, case and punctuation, because PDF text breaks words at
  line ends. The share of quotes found is reported as a quality signal, and only found
  quotes are shown to the model when it proposes terms.
- **Input per record:** title, year, type, language, keywords, container and abstract,
  plus the first 6,000 characters of page text, all capped at 12,000 characters. The
  `pages` table is used only if it exists, so the step runs before and after PR #42.
- **Structured output:** `tag/llm.py:chat_json` sends the pydantic model's JSON schema
  as `response_format` and validates the reply. It tries again once if the reply is
  invalid, and refuses an answer cut off at `max_tokens`. qwen3's thinking is turned off
  via `vocab_extract.extra_body`. #15 reuses the helper.
- **Clustering: average linkage (UPGMA) on cosine similarity, stopping at
  `cluster_threshold` (0.80)**, per kind, over distinct normalised labels.
  - It is implemented in numpy, which is a new dependency.
  - Single linkage, or connected components over a threshold, chains labels that are
    each close to the next into one large cluster. Average linkage does not.
  - The full similarity matrix is small at a few thousand labels.
  - Vectors are cached in `vocab_label_vectors`.
- **Counts come from code.** n_records, n_labels, example labels and record IDs are
  computed from `vocab_labels`. The model only names a cluster, defines it and compares
  it with the draft terms (`new`, `merge_into_existing` or `split_existing`).
  - An `existing_term` that is not in the draft is dropped, and the code adds a note.
  - Clusters seen in fewer than `min_records` (3) records are listed as `unclustered`
    without a proposal.
  - Proposals are cached by a hash of the cluster's members.
- **Output:** `vocab/candidates.yaml` is committed. It is a generated draft for curation
  and is not edited by hand. Its header records the code, vocab, model and prompt
  versions, the sample settings and strata, and the denominator (records labelled).
- **Working tables stay private to `tag/`:** `vocab_sample`, `vocab_labels`,
  `vocab_label_state`, `vocab_label_vectors` and `vocab_proposals`. They are defined in
  `chatcde.tag.tables` and are not part of `schemas.py`. `vocab_label_state` records ok
  or error per record, model and prompt version, which makes resuming and retrying
  failures simple.

## Consequences
- **First run (2026-11-08):**
  - Labelling: 250 records with no failures. There were 4,350 labels, and 98% of quotes
    were found in the input.
  - Clustering at 0.80: 1,837 clusters across the six kinds, of which 353 are seen in at
    least 3 records.
  - The largest clusters match the draft terms well, such as "Literature and systematic
    review", "Case study" and "Mountains". They also add terms that are missing, such as
    transdisciplinary research, Global South / Global North, food security,
    deforestation, and soil and water conservation.
  - With `vocab_propose.v1`, definitions had a median of 28 words, and 308 of 353
    candidates had a note (median 45 words), mostly explaining the model's reasoning.
    That made the file 450 KB.
- **`vocab_propose.v2` (same day):**
  - Definitions are capped at 30 words.
  - A note is written only when the curator must act on it, in at most 20 words: a
    mixed cluster, labels too vague to use, or places named instead of kinds of place.
  - Result: definitions now have a median of 18 words, only 15 candidates have a note,
    304 of 353 labels are unchanged, and the file is 315 KB. v1 stays in the repo for
    provenance.
- **The checks run when the file is written, not before caching.**
  - `vocab_proposals` keeps the model's own answer, so a better check needs no new
    model calls.
  - `resolve_term` matches an existing term ignoring case and the `code: ` prefix. The
    model wrote "Sustainable land use" for "SL-a: Sustainable land use" twice.
- The developer curates from evidence: each candidate has a frequency in the sample,
  example labels and record IDs to check.
- The counts in candidates.yaml describe the sample, not the corpus. Strata are
  oversampled on purpose, so the frequencies are not corpus shares. Corpus counts come
  only from tagging (#15) and `count_by`.
- Labels from records without page text rest on title and abstract only. Records with
  page text are preferred within each stratum.
- Revisit if the prototype continues:
  - Tune `cluster_threshold` by inspecting candidates.yaml, since 0.80 is a first guess
    for qwen3-embedding-4b at 1,024 dimensions.
  - Run the label step a second time, with the alternative tagging model or another seed,
    to see which candidates are stable.
  - Add `scale` as a seventh kind if its seed list proves weak in tagging.
  - Share `load_prompt` with `chatcde.parse.ocr` once PR #42 is merged.
