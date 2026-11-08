# 0014: Parsing PDFs: PyMuPDF text layer, vision-model OCR for scanned and garbled pages

- Status: accepted
- Date: 2026-11-07

## Context
Issue #10 adds `chatcde parse`: each downloaded PDF (decision 0009) becomes one `PageText`
per page in the `pages` table, which Track B chunks (decision 0011). Pages without a usable
text layer go to the vision model (config role `vision`). The parser choice must be recorded.

A read-only survey of the 1,542 PDFs (2026-11-07) found 47,596 pages (median 15 per document,
largest 1,242). None fails to open, and none is encrypted. 2,495 pages have fewer than 50
characters; 1,644 of these contain images. 8 documents have no text layer at all, and 331
have some empty pages. MuPDF prints many harmless repair warnings. Tesseract is installed
with English only, and the corpus is EN/DE/FR/ES plus some Burmese and Portuguese.

BORIS language codes are per record and partly wrong. The 49 records coded `me` have
English, German, Spanish and French titles; `me` probably means *mehrsprachig*, used for
series published in several languages. So `me` cannot be mapped to one language.

## Decision
- **Parser: PyMuPDF**, `page.get_text("text")` in the PDF's own reading order, text kept as
  extracted (the chunker cleans it). It reads all 47,596 pages in about 6 minutes on a
  laptop and needs no ML models. It is also the extractor the fixtures were made with: all
  126 real fixture pages match the parsed pages after NFKC and whitespace normalisation.
  PyMuPDF is AGPL-3.0, which is compatible with this project's GPL-3.0-or-later.
  Rejected: docling (torch-sized, much slower on CPU, and its Markdown/layout output does
  not fit `PageText.text`); pdfplumber/pdfminer (slower, no page rendering for OCR).
- **Page classes** (`chatcde.parse.pdf.classify`; thresholds in the `parse` config):
  - `text`: the text layer is used.
  - `ocr`: fewer than `min_chars` (50) non-space characters, and images covering at least
    `min_image_coverage` (20%) of the page, or at least `min_drawings` (200) vector paths
    (outlined text).
  - `garbled`: garbage characters (U+FFFD, private-use, control, `(cid:NN)`) above
    `max_garbage_ratio` (5%), or word characters (letters, digits, combining marks) below
    `min_alnum_ratio` (50%) of the characters that are not space or punctuation.
    Punctuation is left out so dot leaders in forms and contents pages don't count, and
    marks are counted so Burmese text isn't flagged (both seen in the first runs).
  - `blank`: low text and nothing to read. Stored with its few characters and no model call.
  Every page gets a row, so page numbers stay complete.
- **OCR with the vision model, not Tesseract**: `ocr` and `garbled` pages are rendered at
  `ocr_dpi` (150), with the long side capped at `ocr_max_px` (2000), and sent to
  `models.vision` (qwen3-vl-30b-a3b-instruct) via `/chat/completions`. The prompt is
  versioned: `src/chatcde/prompts/ocr_page.v1.md`. It asks for a faithful transcription in
  the original language, or `[NO TEXT]`. An answer cut off at `ocr_max_tokens` counts as a
  failure, because cut-offs are loops (one phrase repeated to the limit), not long pages;
  such a page is asked once more with vLLM's `repetition_penalty`
  (`ocr_retry_repetition_penalty`, 1.05), which is not used on first attempts so normal
  pages are unaffected. Answers whose main Unicode script is in `ocr_reject_scripts`
  (`THAI`) are refused, also when they come from the cache: qwen3-vl writes Lao pages as
  plausible Thai, and no record in the corpus contains Thai text. Up to
  `llm.max_concurrency` (4) requests run at a time. After `max_consecutive_failures` (5)
  *endpoint* failures in a row the pass stops; unreadable pages don't count. Tesseract would need
  language packs per language and does poorly on multi-column scans. The vision model is
  already on GPUStack and handles all the corpus languages.
- **The vision client** is Track B's `EndpointClient` (certifi TLS, retries), used as it is.
- **OCR cache**: `data/text/ocr/<record_id>.jsonl`, keyed by the SHA-256 of the rendered
  PNG, the model and the prompt version. `--force` re-reads PDFs but never asks twice for
  the same page. A new model or prompt version asks again.
- **Two passes**: pass 1 writes the text layer of every page and lists `ocr`/`garbled`
  pages as pending. Pass 2 fills pending pages from the cache or the model. `--no-ocr`
  runs pass 1 only (plus cache hits), so the text layer is available without the endpoint.
  Until OCR succeeds, a pending page keeps its text-layer text.
- **Language per page**: lingua (`lingua-language-detector`), restricted to `parse.languages`
  (en, de, fr, es, it, pt), on the first 2,000 characters. Pages under 40 characters, or
  with confidence under 0.5, get `null`, and the chunker then uses the record's BORIS
  language. Garbled text-layer pages get `null` until OCR. BORIS's language field is
  left as recorded.
- **Provenance**: every page has the code version and time; OCR pages also carry
  `model = models.vision` and `prompt_version = parse.ocr_prompt`. `Provenance` has no
  parser field, so the parser name/version and `PARSER_VERSION` go in `parse_state` and the
  summary. This avoids a `schemas` change.
- **Idempotency**: a Track A side table `parse_state` (as `chunk_state` in 0011) holds, per
  record, the PDF's sha256, `PARSER_VERSION`, the pass-1 settings, page counts by class,
  the pending OCR pages and the status (`done`, `needs_ocr`, `error`). A rerun skips
  records whose PDF, parser version and settings are unchanged and whose pages are all
  stored. `needs_ocr` records only go through pass 2, and `error` is retried. Each record
  is written in one transaction. Records whose PDF is no longer downloaded lose their
  pages. Counts go to `data/text/parse_summary.json`.

## Results (text layer, `chatcde parse --no-ocr`, 2026-11-07)
- 1,542 PDFs, 47,596 pages, 0 errors; 1,308 records `done`, 234 `needs_ocr`.
- Pages by class: text 44,742; ocr 1,719; garbled 155; blank 980. 1,874 pages wait for OCR.
- Garbled pages are mostly contents pages of the UNEP *Global Environment Outlook 7*
  chapter PDFs, whose dot leaders are encoded as `\x08` + U+FFFD (about 18 per chapter).
- Pages by detected language: en 34,180; de 4,554; fr 3,092; es 2,466; pt 400; it 40;
  none 2,864 (short, blank, garbled or another script such as Burmese).
- BORIS language × main detected language (by characters) for the 49 `me` records:
  de 14, fr 10, en 8, es 1, none 1 (15 have no PDF). For records coded `en`, 1,203 of
  1,232 are detected as English. For records coded `de`, 188 of 201 are detected as German.

## Results (OCR pass, `chatcde parse`, 2026-11-07)
- First pass: 1,847 requests in 42 minutes (about 1.4 s per page with 4 in parallel),
  1,791 pages transcribed. Dropped connections and broken pipes were retried successfully.
- 83 pages hit the 4,096-token cut-off, all loops: 47 in the two Lao records, 36
  elsewhere. The repetition-penalty retry rescued 14 of those 36. About 20 Lao
  pages had been "transcribed" as Thai.
- Final state: **1,801 of 1,874 pages transcribed**; 1,527 records `done`, 15 `needs_ocr`.
  73 pages stay pending: 51 Lao pages (Thai output, in 2 records) and 22 pages that still
  loop (dense maps, data tables, a cover). They keep their text-layer text, which is
  empty or garbled.
- Hand check: a German survey form, two pages of fully scanned book chapters and a GEO-7
  list of figures (garbled leaders) match the rendered pages, including footnote
  numbers and dot leaders. A 19-page scanned chapter and a German article were also
  checked in the trial.
- Pages by detected language after OCR: en 35,250; de 4,657; fr 3,127; es 2,526; pt 393;
  it 41; none 1,602.

## Consequences
- Full text for all 1,542 downloaded records is in `pages`. Chunking (0011) picks it up
  through its fingerprint, with no change needed on Track B's side.
- OCR costs about 1,900 vision requests for the current corpus. The cache makes this a
  one-off.
- `src/chatcde/prompts/` belongs to Track B. The OCR prompt is the first Track A prompt
  there; agreed in the PR for #10. Tagging prompts will follow the same arrangement.
- Changing the classification or extraction code requires bumping `PARSER_VERSION` in
  `src/chatcde/parse/pdf.py`. Settings changes are detected automatically.
- Every `chatcde parse` run asks again for the 73 pending pages (about 10 minutes, mostly
  the Lao records). Use `--no-ocr` when only new PDFs need parsing.
- Revisit: stop retrying a page after N failed attempts (needs an attempts column in
  `parse_state`); try `model_alternatives.vision` (internvl3-8b) on the Lao pages and the
  loops; detect broken font encodings that produce plausible Latin letters (not caught today);
  add Burmese and other scripts to `parse.languages` if they matter; use the record's
  detected languages to correct BORIS codes in `chatcde coverage`; keep figures' text
  separate from body text if it crowds search results.
