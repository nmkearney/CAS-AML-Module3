# 0025: `chatcde evaluate questions`: question format and code-only scoring

- Status: accepted
- Date: 2026-11-08

## Context
Issue #28: run `tests/questions.yaml` through the agent, score the answers and compare
the agent models. Build step 4 passes when the system answers the 20–30 test questions.
Track A writes the questions (#19), which gives no format yet, so this decision sets
it. Scores should not depend on another model's opinion. Counts and record IDs can be
checked exactly.

## Decision
- **Format** (`version: 1`; unknown keys are errors, so typos fail loudly):
  ```yaml
  version: 1
  questions:
    - id: q01
      question: "What has CDE found on large-scale land acquisitions in Laos?"
      kind: synthesis          # lookup | search | count | gaps | synthesis
      expect:                  # every key optional
        records_any: [<record_id>, ...]   # at least one must be cited
        records_all: [<record_id>, ...]   # all must be cited
        tools: [search_publications]      # must be called successfully
        mentions: ["Laos"]                # all must appear (case-insensitive)
        mentions_any: ["concession", "land deal"]
        numbers: [42]                     # must appear in the answer
      notes: "why these are the right answers"
  ```
  Record IDs are BORIS UUIDs.
- **Scoring is code only; there is no LLM judge.**
  - Each `expect` key is one check.
  - Three more checks apply to every answer: `answered` (no used-up budget or cut-off),
    `no_unknown_ids`, and `cited_from_tools` (CLAUDE.md: every answer traceable).
  - An answer **passes** when all its checks pass. Its **score** is the share of
    checks passed, and its **record recall** is the expected records it cites.
  - Numbers are read from the answer without its `[BORIS:…]` references, and
    thousands separators (`1,234`, `1'234`, `1 234`) are ignored.
- **Comparing models:** `--models both` (default) asks every question with
  `models.agent` and `model_alternatives.agent`, each question in a fresh
  conversation, and `--runs N` repeats each one. A table per model gives:
  - pass rate, mean score and record recall;
  - tool calls, seconds and tokens;
  - errors.
- **Resumable results:**
  - every answer is appended as a JSON line (score and full `AgentResult`) to
    `--out`, by default a new file in `evaluation.results_dir`;
  - rerunning with the same file skips the (question, model, run) answers already
    there;
  - endpoint errors are recorded as failed answers;
  - `--rescore FILE` scores saved answers again after the expectations change, with
    no model calls.
- **Threshold:** `evaluation.min_question_pass_rate` (0.8, to be confirmed after the
  first real run). The command exits 1 if any model is below it, and 2 if the
  questions file is missing or invalid.

## Consequences
- Synthesis quality (is the summary right and complete?) is only partly measured, by
  required records and words. Good expectations matter: prefer `records_any` and
  `mentions_any` over exact wording.
- Expected counts change when the data or the tags change. `notes` should say how a
  number was obtained (for example the `chatcde count` command), so it can be updated.
- Not yet run for real: there is no `tests/questions.yaml` and no catalogue on the
  developer's laptop yet.
- Revisit:
  - an optional LLM judge for synthesis, reported separately from the code checks;
  - questions that run `chatcde gaps` (0024) instead of the free agent;
  - the threshold.
