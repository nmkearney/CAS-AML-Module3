# 0001: Command-line interface first; every LLM call local

- Status: accepted
- Date: 2026-10-27

## Context
ChatCDE is a prototype for a demonstration, built by one developer without staff
workshops. Options for the interface were a command line, an MCP server for existing
chat clients, and a web app. For the LLM, build-time tagging could have used a
cloud model (the publications are public), or stayed local.

## Decision
- Researchers use ChatCDE through the `chatcde` command line. Tools are plain
  Python functions in `chatcde.tools`, so a web app or MCP server can wrap them later.
- Every LLM call, in the pipeline and at question time, goes to a local
  OpenAI-compatible endpoint (GPUStack, vLLM or Ollama) set in the config.

## Consequences
- Simplest build: no front-end code; effort goes into the pipeline and tools.
- The demonstration audience must be comfortable with a terminal, or the demo is
  run by the developer.
- Tagging quality depends on the local model available; `chatcde evaluate`
  measures it. If quality is too low, revisit (larger local model, or tag from
  abstracts plus first pages only).
- Revisit if the prototype continues: a web app for non-technical users.
