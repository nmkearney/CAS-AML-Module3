# 0033: One developer after the coursework

- Status: accepted
- Date: 2026-11-11

## Context
ChatCDE was built for the CAS AML Module 3 coursework by two developers on two
tracks: Track A (data, Norman Kearney) and Track B (assistant, Simon Bader).
They met at a shared contract (`schemas.py`, `tools/core.py`), changed it only
through issues labelled `schemas`, and every pull request needed the other
developer's approval. The coursework is finished, and Norman Kearney continues
alone. The state at the end of the coursework is tagged `coursework-final`.

The two-person rules no longer work: nobody else can approve a pull request,
and track ownership no longer means anything.

## Decision
- **Repository** renamed from `nmkearney/CAS-AML-Module3` to `nmkearney/chatcde`.
  GitHub redirects the old URL.
- **No tracks.** One developer works on every folder. The old track labels and
  `cross-check` issues are retired; a new issues list replaces them.
- **Pull requests stay.** `main` is still changed only through a pull request with a
  green `ci` check, and still can't be deleted or force-pushed. No approval is
  required (ruleset `protect main`: 0 approvals, no code-owner review).
- **Branches** are named `<issue>-short-name` (no `a/` or `b/` prefix).
- **Shared contract.** `schemas.py` and the tool signatures in `tools/core.py`
  still change only on purpose: in their own pull request, with a decision record
  when the change isn't obvious, fixtures and tests updated alongside.
- **Credit.** Simon Bader stays a listed author in `pyproject.toml`; the README
  credits him for Track B (store, tools, agent, workflows). The code is GPL-3.0.
- Simon keeps collaborator access for now.

## Consequences
- Simpler workflow, but no second reviewer. The cross-checks (#19 test questions,
  #29 independent reference tagging) lose their independence; evaluation must
  say that the reference set and questions were written by the developer.
- `CONTRIBUTING.md`, `CLAUDE.md`, `docs/planning.md` and the PR template are
  updated to match. `scripts/create_github_issues.py` and `scripts/issues.yaml`
  are kept as a record of the coursework setup.
- Revisit if another developer joins: restore 1 approval and CODEOWNERS.
