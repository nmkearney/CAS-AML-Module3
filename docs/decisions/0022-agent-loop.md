# 0022: Agent loop, tool definitions and citation format

- Status: accepted
- Date: 2026-11-08

## Context
Issue #25 asks for the agent loop over the local OpenAI-compatible endpoint. It should
use the `agent` role model (gpt-oss-120b; alternative qwen3.8-27b) and a tool-call
budget. Tool definitions should be generated from the tool functions' signatures and
docstrings, and answers should follow a citation format. GPUStack supports tool calling
with `tool_choice: auto` for both agent models and returns gpt-oss's reasoning in a
separate field (0003). The tools take their config from a `ToolContext` (0020) and
raise `ToolError` with a message that says what to do instead. CLAUDE.md requires that
every answer can be traced to BORIS IDs and that counts never come from the LLM.

## Decision
- **Tool definitions come from the code** (`chatcde.tools.specs`):
  - each core function's type hints become a pydantic argument model, whose JSON
    schema is the tool's `parameters`, with `$ref` inlined, titles removed and
    `additionalProperties: false`;
  - the docstring's text before `Args:`, plus its `Returns:`, is the tool's
    description, and each `Args:` entry describes its parameter.

  The same argument model validates the model's arguments, so
  `{"row": "sub_area", "filters": {...}}` becomes a `Dimension` and a `Filters`. The
  model sees exactly what the code accepts, and the definitions cannot drift from the
  functions. An MCP server can reuse this later.
- **Which tools:** `agent.tools` in the config, all seven core tools by default.
  `coverage_report` reports "not available yet" until Track A's #45 is merged.
- **Running a call never crashes the conversation.** Invalid JSON, invalid arguments,
  an unknown tool, a `ToolError`, `NotImplementedError` or an unexpected exception go
  back to the model as `{"error": "..."}` tool messages. Unexpected exceptions are
  logged with their traceback, and the model gets only their type.
- **Results are compacted before they reach the model:**
  - fields in `agent.omit_fields` (`provenance`, `harvested_at`) are dropped;
  - `record_ids` lists are cut to `agent.max_ids_per_list` (20), with
    `record_ids_omitted` giving the rest, so counts stay exact;
  - the JSON is cut at `agent.max_tool_result_chars` (16,000), with a note that asks
    for a narrower call.

  The full result stays in the code for checking citations.
- **The loop:** the system prompt is a versioned file (`prompts/agent_system.v1.md`,
  `agent.system_prompt`). Each turn is one request to `/chat/completions` with
  `models.agent`, `agent.temperature` (0.2), `agent.max_tokens` (4,096, because gpt-oss
  reasons first) and `agent.extra_body`. The model's tool calls run one after another
  (the catalogue is read-only and the order stays reproducible). The model's reasoning
  is not sent back.
- **Budget:** at most `agent.max_tool_calls` (12) calls per question. Calls beyond the
  budget in the same reply are answered with an error. After the last call, a user
  message (`BUDGET_NOTE`) says the budget is used up, and the next request is sent
  without tools, so the model has to answer. `stop_reason` is `answered`, `budget` or
  `length` (the answer was cut off).
- **Citation format: `[BORIS:<record_id>]` or `[BORIS:<record_id>, p. <n>]`**, several
  per bracket allowed. The record_id is what every tool returns, so the model copies it
  rather than inventing a short form. The form is easy to find with a regular
  expression and does not clash with Markdown. After each answer, the cited IDs are
  looked up in the catalogue. Each citation gets the full `Citation` (title, year, DOI,
  BORIS URL), the pages cited, and `from_tools`: whether a tool returned that record in
  this conversation. Cited IDs that are not in the catalogue go in `unknown_ids`, and a
  warning is logged.
- **Provenance:** `AgentResult` records the model, prompt version, vocabulary version,
  code version, every tool call (name, arguments, success, time, size) and token usage.
  `chatcde evaluate questions` (#28) can then compare models and prompt versions.
- **Conversations:** an `Agent` keeps the message history across `ask()` calls (for
  `chatcde chat`), including tool messages, and `reset()` clears it. Rendering for the
  terminal, with numbered references and the edition and coverage note, is #26.

## Consequences
- Adding a tool to the agent means writing a typed, documented function in `core.py`
  and adding its name to `agent.tools`. The docstrings are now prompts: changing one
  changes the agent's behaviour, and the change is visible in review.
- Not yet verified against GPUStack: the loop is tested with a scripted fake endpoint.
  On the VPN, check:
  - that gpt-oss follows the citation format and uses the `[BORIS:…]` IDs it was given;
  - whether `reasoning_effort` in `agent.extra_body` helps;
  - how many tool calls typical questions need.
- Long chats grow with every tool result. If they reach the context limit, summarise
  or drop old tool messages (revisit with #26).
- Revisit:
  - parallel tool calls if latency matters;
  - a short ID alias if the model miscopies UUIDs;
  - `tool_choice: required` on the first turn if the model answers without looking
    things up.
