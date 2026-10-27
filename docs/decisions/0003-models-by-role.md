# 0003: GPUStack models by role

- Status: accepted (to be confirmed by `chatcde evaluate`)
- Date: 2026-10-27

## Context
UniBe's GPUStack serves these models (context as configured on GPUStack):
LLMs gpt-oss-120b (78K), minimax-m2.7 (98K), qwen3.8-27b (188K),
lfm2.5-24b-a2b (125K), qwen3-vl-30b-a3b-instruct (64K, vision),
internvl3-8b-instruct (64K, vision); embeddings qwen3-embedding-4b (31K),
granite-embedding-107m-multilingual (1K); reranker
jina-reranker-v2-base-multilingual (1K); speech faster-whisper-large-v3.

## Decision
Each role has a first choice and an alternative, set in `config/default.yaml`:

| Role | First choice | Alternative |
|---|---|---|
| Agent (tool calling, answers) | gpt-oss-120b | minimax-m2.7 |
| Bulk tagging (JSON output) | qwen3.8-27b | lfm2.5-24b-a2b |
| Embeddings | qwen3-embedding-4b | granite-embedding-107m-multilingual |
| Reranking | jina-reranker-v2-base-multilingual | none |
| Scanned pages, figures | qwen3-vl-30b-a3b-instruct | internvl3-8b-instruct |
| Audiovisual (optional) | faster-whisper-large-v3 | none |

## Consequences
- Chunks of about 500 tokens, to fit the reranker's 1K context.
- To verify before relying on it: tool calling and JSON-schema output
  (`response_format`) are enabled for the agent and tagging models on GPUStack;
  GPUStack's concurrency limit (sets tagging time).
- `chatcde evaluate` compares first choice and alternative on the reference set
  (tagging) and `tests/questions.yaml` (agent); update this record with results.
