# Contributing to ChatCDE

Two developers build ChatCDE in parallel, on two tracks that meet at the shared data
formats in `src/chatcde/schemas.py`. This guide covers who owns what, the day-to-day
workflow, and the one-time GitHub setup.

## Tracks and ownership

| | Track A: Data | Track B: Assistant |
|---|---|---|
| Developer | Norman Kearney (`@nmkearney`) | Simon Bader (`@krybad`) |
| Owns | `harvest/`, `parse/`, `tag/`, `vocab/`, `chatcde export`, `chatcde fetch-data` | `store/` (chunks, embeddings, search, reranking), `tools/`, `agent.py`, `workflows/`, `prompts/`, query commands |
| Build steps | 2 (harvest and parse), 3 (vocabulary and tagging) | 4 (tools, agent and CLI) |
| Writes | `records`, `authors`, `tags`, `pages`, `downloads` tables; data bundles | `chunks` table; search index |
| Checks | `chatcde coverage`, `chatcde evaluate tagging` | unit tests on fixtures, `chatcde evaluate questions` |
| Cross-check of the other track | Writes `tests/questions.yaml` (20–30 questions with known answers) | Hand-tags 20–25 records of the reference set |

Shared: `schemas.py`, `config/`, `cli.py`, `pyproject.toml`, `docs/`, `tests/fixtures/`.
Steps 1 and 5 are done together.

## Day 1 (together)

1. Do the one-time GitHub setup below.
2. Create the issues: `python scripts/create_github_issues.py --assign-a @me --assign-b @krybad`
   (run with `--dry-run` first; needs `gh auth login`).
3. Build step 1: `pyproject.toml`, the `chatcde` command, config loading, logging, ruff, pytest.
4. Read the draft `src/chatcde/schemas.py` and `src/chatcde/tools/core.py` together and agree
   on them. Merge them in one pull request approved by both.
5. Build `tests/fixtures/`: about 30 real records with hand-made tags and a few pages of text,
   so Track B can work without waiting for the real pipeline.

## Virtual environment
Create a virtual environment in the local project folder. .venv is already added to .gitignore.

cd [project path on your device]
python3 -m venv .venv
source .venv/bin/activate

yaml is needed, so install it within the VE:

pip install pyyaml

## Everyday workflow

Never commit directly to `main`. Every change goes through a branch and a pull request (PR).

```bash
# 1. Start from an up-to-date main
git switch main
git pull

# 2. Make a branch named after the track and issue: a/<issue>-short-name or b/<issue>-short-name
git switch -c a/7-download-pdfs

# 3. Work, then commit in small steps
git add src/chatcde/harvest/
git commit -m "Download ORIGINAL bitstreams with a manifest"

# 4. Push the branch and open a PR
git push -u origin a/7-download-pdfs
gh pr create --fill          # or use the "Compare & pull request" button on GitHub
```

In the PR description, write `Closes #7` (the issue number). GitHub then links the PR to the
issue and closes the issue when the PR is merged. The PR template lists what to check.

After approval and a green CI check, merge with **Squash and merge** (one tidy commit on
`main`), delete the branch, and update your local copy:

```bash
git switch main
git pull
git branch -d a/7-download-pdfs
```

If `main` moved on while you worked, bring your branch up to date before merging:

```bash
git switch a/7-download-pdfs
git merge main        # fix any conflicts, then commit
git push
```

## Reviewing a pull request

Every PR needs the other developer's approval; with two people, that means both of you have agreed.

- **On GitHub:** open the PR → **Files changed** → click a line to comment → **Review changes**
  (top right) → choose **Approve** or **Request changes** → **Submit review**.
- **In the terminal:**
  ```bash
  gh pr list                       # open PRs
  gh pr checkout 12                # try the branch locally
  pytest && chatcde --help         # run what the PR says to check
  gh pr review 12 --approve        # or: --request-changes --body "…"
  ```

Review for: does it do what the issue asks, are there docstrings and tests, are settings in the
config rather than in the code, and is anything in `schemas.py` changed without discussion.

## Changing shared formats

`src/chatcde/schemas.py` and the tool signatures in `src/chatcde/tools/core.py` are the contract
between the tracks.

1. Open an issue labelled `schemas` describing the change and why.
2. Agree on it in the issue before writing code.
3. Make the change in its own small PR, with the `schemas` label, updating both tracks' code and
   the fixtures if needed.

## Issues, labels and milestones

- **Issues** are the to-do list: one per task. Open one with **Issues → New issue** or
  `gh issue create`. Assign it to whoever does it, add a label and a milestone.
- **Labels:** `track:A-data`, `track:B-assistant`, `joint`, `schemas`, `decision` (needs a record
  in `docs/decisions/`), `cross-check`.
- **Milestones** are the five build steps. **Issues → Milestones** shows progress per step.
- **See your work:** `gh issue list --assignee @me`, or filter on GitHub by label.
- **Optional board:** **Projects → New project → Board** gives To do / In progress / Done columns;
  add the repository's issues to it.
- Discuss in the issue's comments, not in chat, so decisions stay with the work.

## Code standards

- Python 3.12+. Type hints on all functions. Google-style docstrings that say what and why.
- `ruff check .` and `ruff format .` before committing. CI runs both, plus `pytest`.
- Tests next to the code they test, in `tests/`, using the fixtures. No network in unit tests.
- Settings in `config/default.yaml`. Personal settings (GPUStack key, local paths) in
  `~/.config/chatcde/config.yaml` or environment variables, never committed.
- Notebooks are for exploring only; anything that matters becomes a function or command.
- A non-obvious choice gets a short record in `docs/decisions/` (template in its README).
- Never commit `data/`, PDFs, extracted text or indices (`.gitignore` covers them).

## Working with Claude Code

Both of you share `CLAUDE.md`. Start a session with, for example:

> I work on track A. Read CLAUDE.md and CONTRIBUTING.md, then work on issue #7.

Keep each session to your own track's folders. If a change to `schemas.py` seems needed,
open an issue instead of editing it.

## One-time GitHub setup (repository owner)

### 1. Add your colleague

**Settings → Collaborators → Add people** → enter their GitHub login. They accept the email
invitation. Then fill in their login in this file, in `.github/CODEOWNERS` (remove the `# `
at the start of each line to activate it) and in the `--assign-b` command above.

### 2. Protect `main`

**Settings → Rules → Rulesets → New ruleset → New branch ruleset**:

- **Ruleset name:** `protect main`; **Enforcement status:** Active.
- **Target branches → Add target → Include default branch.**
- Tick **Restrict deletions** and **Block force pushes**.
- Tick **Require a pull request before merging**, then:
  - **Required approvals:** 1 (the author can't approve their own PR, so this means the other developer).
  - Tick **Dismiss stale pull request approvals when new commits are pushed**.
  - Tick **Require review from Code Owners** (uses `.github/CODEOWNERS`).
- Tick **Require status checks to pass** → **Add checks** → choose `ci`. The check appears in
  the list only after the CI workflow has run once, so add it after the first PR.
- Leave the **Bypass list** empty, so the rules apply to both of you.
- **Create.**

Older guides describe the same thing under **Settings → Branches → Add branch protection rule**;
either works.

**Private repositories:** GitHub Free enforces rulesets only on public repositories. For a
private repository you need GitHub Pro or Team. GitHub Pro is free for students and teachers
through GitHub Education (education.github.com). Without it, follow the same rules by agreement.

### 3. Labels, milestones and issues

Run `python scripts/create_github_issues.py` (see Day 1). It is safe to re-run: existing
milestones and issues are skipped. Edit `scripts/issues.yaml` to add or change issues later,
or create them on GitHub directly.

### 4. Merge settings

**Settings → General → Pull Requests:** allow **Squash merging** (you may untick the other two)
and tick **Automatically delete head branches**.
