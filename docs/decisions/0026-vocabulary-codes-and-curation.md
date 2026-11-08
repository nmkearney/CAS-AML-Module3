# 0026: Vocabulary codes and curation in an Excel workbook

- Status: accepted. The change to `Tag.term` in the shared formats was proposed in #49
  and is made in PR #55 (fixtures remapped to codes).
- Date: 2026-11-08

## Context
Issue #14 turns the 353 candidates from open extraction (`vocab/candidates.yaml`, 0019)
into the curated vocabulary v0.3 that tagging (#15) uses as its closed lists. There are
no workshops (0002): the developer decides, and the decisions must stay traceable so a
later phase can revisit them.

Two problems with the v0.2 file:
- **No codes for most terms.** method, data_type, setting, scale, system, actors and
  justice_lens have no codes, so tags and the test fixtures store the label itself.
  Curation renames and merges terms, which would break every existing tag.
- **Hand-editing is slow and leaves no record.** Editing a YAML file of several hundred
  terms is slow and error-prone, and it leaves no record of why each term exists.

## Decision
- **Every term in a curated dimension gets a stable code** of the form
  `<PREFIX>-<kebab-slug>`, e.g. `MET-remote-sensing`, `SET-mountains`,
  `ASA-food-security`.
  - Prefixes: MET (method), DAT (data_type), SET (setting), SCA (scale), SYS (system),
    ACT (actors), JUS (justice_lens), ASA (additional_sub_area, 0027).
  - Groups get codes too (`MET-G-<slug>`), so gaps can be counted per group.
  - Strategy dimensions keep their codes (JE, SL-a, O2, M1, TA1). Country, region and
    SDG keep their standards.
  - `Tag.term` stores the code (#49).
- **Codes are never reused.** A term that is merged or retired moves to the dimension's
  `retired:` list, with `replaced_by` (or null), so old tags can be mapped forward.
- **New layout for the curated dimensions:**
  `{groups: [{code, label}], terms: [{code, label, group, definition, includes, related,
  source, note}], retired: [...]}`.
  - `includes` lists merged labels and synonyms (at most 12, deduplicated ignoring
    case). They help the tagging prompt.
  - `related` lists codes of related terms or sub-areas (a pointer, not a merge; see
    below).
  - `source` is `seed` (from the v0.2 draft) or `candidate` (from open extraction).
  - `actors` keeps `roles`.
- **Decide in Excel, apply with code. The flow runs one way:**
  `candidates.yaml` + YAML → `chatcde vocab review` → `vocab/curation/v0.3.xlsx`
  (decide here) → `chatcde vocab apply` → `draft_vocabulary.yaml`.
  - **The workbook has one sheet per curated dimension,** plus `sub_area_scope` (to
    sharpen scope notes) and a README.
    - Rows are current terms (decision preset to `keep`) and candidates (decision
      empty).
    - White columns are decisions: decision, code, label, definition, group,
      impact_area, merge_into, comment.
    - Grey columns are the model's evidence and are never read back.
    - The decision, impact_area and group columns have dropdowns. Code, label and
      definition are pre-filled from the candidate. The definition of a current term
      is pre-filled from a candidate that merges into it.
  - **Decisions:**
    - current terms: `keep` (editing the label renames it; the code stays), `merge`
      (retired, with `replaced_by`), `retire`
    - candidates: `accept`, `merge` (its labels become `includes` of the target, which
      may be a strategy sub-area), `reject`, `defer`
    - `merge_into` on a `keep` or `accept` row means **related**, not merged: the
      term stays and gets a `related` list (codes separated by `;`). Added while
      applying v0.3, where 16 accepted additional sub-areas pointed to a sub-area or
      term (e.g. Biodiversity conservation → SL-b). Re-exporting shows the links again.
    - `merge_into` may hold a label if it names exactly one live term or sub-area; it
      is read as that code, with a warning.
  - **The workbook keeps the vocabulary it was exported from** in a hidden `_base`
    sheet. `vocab apply` builds the curated blocks from that base plus the decisions,
    never from the current file. So:
    - applying twice gives the same file
    - the curated blocks are replaced verbatim
    - every other block and its comments stay untouched, so other PRs that edit those
      blocks (e.g. #47) merge cleanly
  - **`vocab apply` checks every row and writes nothing if any is invalid.** It lists
    all the problems:
    - a missing decision, or one not allowed for the row type
    - a code that is missing, malformed or duplicated
    - an unknown `merge_into` code (or a label that matches no term, or several)
    - a term related to itself
    - a retired code that is still live
    - an additional sub-area without an impact area, or one that repeats a sub-area
      label

    It warns about missing definitions and about term counts outside
    `vocab.terms_per_dimension` (15–40; applied only to dimensions that have
    candidates).
  - **It also writes `vocab/curation/v0.3_codes.csv`** (old label → code, with kept,
    merged or retired), used to remap existing tags and fixtures.
  - `.xlsx`, not CSV, because Excel with Swiss settings saves CSV with semicolons and a
    non-UTF-8 encoding.
- **Rules of thumb for the curator** (also in the workbook's README):
  - Accept or merge candidates seen in at least 5 sampled records, and decide 3–4 case
    by case. Never add a term from the unclustered tail alone.
  - Reject country and place names, which belong under country.
  - Merge near-synonyms and keep their labels as `includes`.
  - Aim for 15–40 terms per dimension, with at most two levels (group, then term).
- **The committed record** is `vocab/curation/v<version>.xlsx` (the decisions and the
  reasons in `comment`), the generated YAML, and a CHANGELOG entry built from apply's
  summary.

## Consequences
- Labels and definitions can be improved at any time without breaking tags. Only a
  change of meaning needs a new code.
- Every term can be traced back to a workbook row: a v0.2 seed or a candidate with its
  sample count and example labels.
- The workbook is binary in git, so a diff shows only that it changed. The reviewable
  change is the YAML diff, and the workbook is the evidence behind it.
- `candidates.yaml` stays a frozen, generated record of the run (0019). Its candidate
  `id` (a hash of kind and member labels) joins it to the workbook, and
  `vocab review --carry-from` copies decisions to a later round (v0.4).
- New dependency: `openpyxl`.
- Revisit if the prototype continues: replace the workbook with a shared review page if
  staff take over curation (planning: staff review replaces developer review).
