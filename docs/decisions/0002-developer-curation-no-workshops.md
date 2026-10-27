# 0002: Developer curates schema and vocabulary; no staff workshops

- Status: accepted
- Date: 2026-10-27

## Context
The original plan had senior researchers set the schema in a workshop and two
people validate tags. For the prototype, no workshops with staff will take place.

## Decision
- The developer sets the 16-dimension schema (from CDE's 2025–2028 strategy) and
  curates vocabularies drafted by the local LLM.
- Validation uses a reference set of about 50 records hand-tagged by the developer, plus
  agreement between two LLM runs, reported by `chatcde evaluate`.
- Every non-obvious choice gets a decision record in this folder.

## Consequences
- Fast, but the categories reflect one person's reading; the gap analysis
  should be presented as a demonstration, not an institutional assessment.
- Revisit if the prototype continues: staff review of the vocabulary, a larger
  reference set tagged by two people.
