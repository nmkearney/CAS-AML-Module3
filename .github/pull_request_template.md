## What this changes

<!-- One or two sentences. -->

Closes #<!-- issue number -->

## How to check it

<!-- Commands the reviewer can run, e.g. `pytest tests/test_harvest.py`, `chatcde coverage`. -->

## Checklist

- [ ] Docstrings and type hints on new functions
- [ ] Tests added or updated; `pytest` passes locally
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] Settings go in `config/default.yaml`, not in code
- [ ] Docs updated (README, `docs/`, a decision record if a non-obvious choice was made)
- [ ] Changes `src/chatcde/schemas.py`? Then both developers approve
