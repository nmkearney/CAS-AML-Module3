# ChatCDE

A local research assistant over the publications of the Centre for Development and
Environment (CDE), University of Bern. It indexes CDE's records from BORIS Portal and
lets researchers ask a local LLM about gaps, frontiers and more, with every answer
traceable to BORIS IDs. **Prototype**; see `docs/planning.md` for the design and
`CONTRIBUTING.md` for the workflow.

## Install

Python 3.12 or newer. Install from a clone, in editable mode (the default config is
read from `config/default.yaml` in the clone):

```bash
git clone https://github.com/nmkearney/CAS-AML-Module3.git ChatCDE
cd ChatCDE
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
chatcde --help
```

Most commands are placeholders for now; each one names the issue that implements it.

## Harvest

On the UniBe network or VPN (the BORIS REST API is closed to other traffic):

```bash
chatcde harvest metadata            # BORIS search -> data/catalogue.duckdb (records, authors)
chatcde harvest metadata --offline  # rebuild the tables from the saved pages, no network
chatcde harvest metadata --force    # fetch every page again
```

The fetch saves every search page under `data/raw/boris/` and resumes if it is
interrupted. It takes about 2.5 minutes for the ~87 pages, with 0.3 s between requests.
Off the VPN it stops with exit code 2. Which records count as CDE's is explained in
`docs/decisions/0008-harvest-metadata.md`. The counts of each run are in
`data/raw/boris/harvest_summary.json`.

## Configuration

Settings are merged from these files, later ones winning:

1. `config/default.yaml`: committed defaults; read it for what can be set.
2. `~/.config/chatcde/config.yaml` (or `$XDG_CONFIG_HOME/chatcde/config.yaml`), if present:
   your personal settings.
3. A file given with `chatcde --config PATH …` or in `$CHATCDE_CONFIG`.

An override only needs the keys it changes:

```yaml
# ~/.config/chatcde/config.yaml
llm:
  base_url: "https://gpustack.unibe.ch/v1"
models:
  agent: "qwen3.8-27b"
```

Unknown keys are errors, so typos are caught. Relative paths are resolved against the
clone, not the current folder. The LLM API key is never put in a file: set the
environment variable named by `llm.api_key_env` (default `CHATCDE_LLM_API_KEY`).

```bash
chatcde config path   # which files are merged
chatcde config show   # the merged result
```

To check that the endpoint and every configured model (`models` and
`model_alternatives`) work, i.e. tool calling, JSON-schema output, embeddings,
reranking and concurrency, run `python3 scripts/check_gpustack.py`. It prints a table
and writes a JSON report to `data/checks/`; `--help` lists the options.

## Logging

Messages go to stderr; results go to stdout, so they can be piped. `-v` shows debug
messages and `-q` only warnings. A full log is kept in `data/logs/chatcde.log`
(set `logging.file: null` to turn it off).

## Development

```bash
ruff check . && ruff format --check .
pytest
```
