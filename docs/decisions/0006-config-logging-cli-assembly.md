# 0006: Config lookup, logging and CLI assembly

- Status: accepted
- Date: 2026-11-03

## Context
Build step 1 (issue #1) needed a config loader with a local override, a logging
setup and a `chatcde` command that two developers extend in parallel without
editing the same file. Users install from a clone in editable mode.

## Decision
- **Config files, later wins:** `config/default.yaml` → `$XDG_CONFIG_HOME/chatcde/config.yaml`
  (default `~/.config/…`) → one explicit file from `--config` or `$CHATCDE_CONFIG`.
  Mappings merge key by key; lists and other values replace. No repo-local override
  and no per-key environment variables; the only secret (the LLM key) comes from the
  variable named by `llm.api_key_env`.
- **Validation:** pydantic models mirror `default.yaml` with `extra="forbid"`, so a
  typo in an override is an error rather than a silent default.
- **Paths:** relative paths resolve against the project root (the folder holding
  `config/`), not the current directory; `~` is expanded.
- **Default config location:** read from the clone (`Path(__file__).parents[2]`),
  not packaged into the wheel.
- **Logging:** stdlib `logging`; Rich console handler on stderr (stdout stays clean
  for results), level from config, `-v`/`-q` override; plain-text rotating file
  `data/logs/chatcde.log` at DEBUG. Not JSON: output provenance is stored with the
  data, so the log only needs to be readable. Noisy HTTP loggers held at WARNING.
- **CLI assembly:** each subpackage's `cli.py` defines a Typer `app`; `chatcde.cli`
  merges them (unnamed `add_typer`) and builds the two cross-track groups: `vocab`
  (list from `tools`, `extract` from `tag`) and `evaluate` (`tagging` from `tag`,
  `questions` from `workflows`). Heavy imports go inside command functions.
- **Placement:** `ask`/`chat` live in `workflows/cli.py`, keeping `agent.py` free of
  CLI code. `export`, `fetch-data` and `info` live in a new `bundle/` subpackage
  (Track A). `coverage` is in `tools/cli.py` but is implemented by issue #11 (Track A).
- **Stubs** exit with status 1 and name their issue.
- **Ruff:** `schemas.py` is excluded until issue #2 formats it and decides
  `str, Enum` vs `StrEnum` (UP042).

## Consequences
- Each track edits its own `cli.py`; `chatcde.cli` changes only when a group is added.
- Wheel installs would not find the default config. Revisit (ship it as package data
  via `importlib.resources`) if ChatCDE is ever distributed beyond clones.
- Add per-key environment overrides (e.g. pydantic-settings) if deployment needs them.
