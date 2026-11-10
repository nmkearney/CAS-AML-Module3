# 0003: GPUStack models by role

- Status: accepted; endpoint verified 2026-11-06 (issue #4). Quality still to be
  confirmed by `chatcde evaluate`.
- Date: 2026-10-27, updated 2026-11-06

## Context
UniBe's GPUStack lists these models (context as configured on GPUStack):
LLMs gpt-oss-120b (78K), minimax-m2.7 (98K), qwen3.8-27b (188K),
lfm2.5-24b-a2b (125K), qwen3-vl-30b-a3b-instruct (64K, vision),
internvl3-8b-instruct (64K, vision); embeddings qwen3-embedding-4b (31K),
granite-embedding-107m-multilingual (1K); reranker
jina-reranker-v2-base-multilingual (1K); speech faster-whisper-large-v3.

On 2026-11-06 the endpoint served 9 of these; **minimax-m2.7 was not served**
(`404 Model not found`).

## Decision
Each role has a first choice and an alternative, set in `config/default.yaml`
(`models` and `model_alternatives`):

| Role | First choice | Alternative |
|---|---|---|
| Agent (tool calling, answers) | gpt-oss-120b | qwen3.8-27b (was minimax-m2.7, not served) |
| Bulk tagging (JSON output) | qwen3.8-27b, thinking off | gpt-oss-120b, reasoning low (was lfm2.5-24b-a2b; decision 0029) |
| Embeddings | qwen3-embedding-4b | granite-embedding-107m-multilingual |
| Reranking | jina-reranker-v2-base-multilingual | none |
| Scanned pages, figures | qwen3-vl-30b-a3b-instruct | internvl3-8b-instruct |
| Audiovisual (optional) | faster-whisper-large-v3 | none |

Endpoint: `https://gpustack.unibe.ch/v1` (OpenAI-compatible; vLLM 0.25.1 behind
it). Reranking at `/v1/rerank` (Jina/Cohere format). `llm.max_concurrency: 4`.

## Verification (2026-11-06)
`scripts/check_gpustack.py`, two runs (reports in `data/checks/`, not committed:
`gpustack-20261106-150106.json`, `gpustack-20261106-150553.json`). Short probes,
one request each; they show the features work, not how well the models tag.

| Model | Check | Result |
|---|---|---|
| gpt-oss-120b | tool call, answer from tool result, `tool_choice="required"` | all pass, 0.3–0.5 s; reasoning returned separately |
| qwen3.8-27b (agent alt) | same | all pass, 1.5 s (14 s with `required`) |
| qwen3.8-27b | `response_format` JSON schema, thinking on | run 1: empty content after 53 s (reasoning used the 2,048-token budget); run 2: valid, 21.5 s, 1,099 tokens |
| qwen3.8-27b | JSON schema, `chat_template_kwargs: {enable_thinking: false}` | valid and correct, 1.9 s, 89 tokens |
| lfm2.5-24b-a2b | JSON schema | valid, 0.3 s, but content weaker: country `LAI` (not ISO), no SDGs |
| qwen3-embedding-4b | EN/DE paraphrase vs unrelated | dim 2560; cos 0.735 vs 0.419 |
| granite-embedding-107m-multilingual | same | dim 384; cos 0.895 vs 0.541 |
| jina-reranker-v2-base-multilingual | German query, English passages | relevant passage first (0.40 vs 0.03) |

Concurrency (qwen3.8-27b, 64-token answers, 2×N requests per level):

| Parallel | req/s | tokens/s | p95 latency |
|---|---|---|---|
| 1 | 0.87 | 41 | 1.2 s |
| 2 | 1.94 | 91 | 1.1 s |
| 4 | 3.56 | 167 | 1.2 s |
| 8 | 1.00 | 47 | 14.9 s |

No errors at any level, but at 8 requests queue and throughput collapses: about 4
parallel requests per model, so `max_concurrency: 4`.

## Consequences
- Chunks of about 500 tokens, to fit the reranker's 1K context.
- **Tagging runs qwen3.8-27b with thinking off** (`enable_thinking: false`): with
  thinking on it is about 11× slower and can return no content when reasoning uses
  the token budget. The tagging stage needs a config setting for this (extra request
  parameters per role); add it with the tagging issue, not here.
- If tagging ever uses thinking, give it a large `max_tokens` and treat
  `finish_reason: length` as a failure to retry.
- The agent alternative qwen3.8-27b shares GPU capacity with tagging; don't
  run agent comparisons during a tagging run.
- Vision and speech were not probed (`--vision` exists for later).
- Rerun `scripts/check_gpustack.py` after any GPUStack change.
- **Tagging alternative changed to gpt-oss-120b (2026-11-09, decision 0029).** On the
  pilot, lfm2.5-24b-a2b wrote explanations where verbatim quotes belong (100% not
  found), so it cannot check the main tagger. gpt-oss-120b, from another model
  family, worked: 0 failures and 15% of quotes not found. It needs its own request
  settings (`tagging.alternative`: `max_tokens` 8192, `reasoning_effort: low`).
  It is also the agent model, so don't run it for tagging while comparing agents.
- `chatcde evaluate` compares first choice and alternative on the reference set
  (tagging) and `tests/questions.yaml` (agent); update this record with results.
