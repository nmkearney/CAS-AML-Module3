# Contributing to ChatCDE

ChatCDE was built by two developers for the CAS AML Module 3 coursework: Norman
Kearney (data: harvest, parse, vocabulary, tagging, bundles) and Simon Bader
(assistant: store, tools, agent, workflows). That state is tagged `coursework-final`.
Since then Norman Kearney develops it alone (decision 0033). This guide covers the
day-to-day workflow and the code standards.

## Set up

Python 3.12 or newer. Create a virtual environment in the project folder (`.venv` is
gitignored) and install the package in editable mode with its dev tools:

```bash
cd ChatCDE
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
chatcde --help
```

Personal settings (GPUStack key, local paths) go in `~/.config/chatcde/config.yaml` or
environment variables, never in the repository.

## Everyday workflow

Never commit directly to `main`. Every change goes through a branch and a pull request
(PR); `main` only accepts a PR whose `ci` check is green.

```bash
# 1. Start from an up-to-date main
git switch main
git pull

# 2. Make a branch named after the issue: <issue>-short-name
git switch -c 7-download-pdfs

# 3. Work, then commit in small steps
git add src/chatcde/harvest/
git commit -m "Download ORIGINAL bitstreams with a manifest"

# 4. Push the branch and open a PR
git push -u origin 7-download-pdfs
gh pr create --fill
```

In the PR description, write `Closes #7` (the issue number), so the issue closes when
the PR is merged. The PR template lists what to check. There is no second reviewer:
read your own diff on GitHub (**Files changed**) before merging.

When CI is green, merge with **Squash and merge**, then update your local copy:

```bash
git switch main
git pull
git branch -d 7-download-pdfs
```

## Changing shared formats

`src/chatcde/schemas.py` (the table and record formats) and the tool signatures in
`src/chatcde/tools/core.py` are what the pipeline, the tools and the stored data all
depend on. Change them on purpose, not as a side effect:

1. Make the change in its own small PR.
2. Update the fixtures (`tests/fixtures/`) and tests in the same PR.
3. Add a decision record in `docs/decisions/` when the reason isn't obvious, and say
   whether stored data (tags, chunks, bundles) needs a migration or a re-run.

## Issues and labels

- **Issues** are the to-do list: one per task (`gh issue create`). Discuss in the issue's
  comments, so the reasoning stays with the work.
- **Labels** mark the area of the code and `decision` (needs a record in
  `docs/decisions/`). `gh issue list --label <name>` filters by label.
- The coursework labels (`track:A-data`, `track:B-assistant`, `joint`, `cross-check`)
  and `scripts/create_github_issues.py` are kept as a record of the coursework setup.

## Code standards

- Python 3.12+. Type hints on all functions. Google-style docstrings that say what and why.
- `ruff check .` and `ruff format .` before committing. CI runs both, plus `pytest`.
- Tests in `tests/`, using the fixtures. No network in unit tests. For catalogue queries
  use the `fixture_db` pytest fixture (a DuckDB built from `tests/fixtures/`; see its README).
- Settings in `config/default.yaml`, never hard-coded.
- Notebooks are for exploring only; anything that matters becomes a function or command.
- A non-obvious choice gets a short record in `docs/decisions/` (template in its README).
- Never commit `data/`, PDFs, extracted text or indices (`.gitignore` covers them).
  The one exception is `tests/fixtures/`: up to 6 pages each of 30 openly licensed
  records, credited in its README (decision 0007).

## Working with Claude Code

`CLAUDE.md` holds the project facts and rules. Start a session with, for example:

> Read CLAUDE.md and CONTRIBUTING.md, then work on issue #7.

## GitHub settings (for reference)

- **Ruleset `protect main`** (**Settings → Rules → Rulesets**): restrict deletions, block
  force pushes, require a pull request (0 approvals, no code-owner review), require the
  `ci` status check. Bypass list empty.
- **Merge settings** (**Settings → General → Pull Requests**): squash merging, and
  automatically delete head branches.
- If another developer joins: set required approvals to 1, tick **Require review from Code
  Owners**, and add them to `.github/CODEOWNERS` (decision 0033).
