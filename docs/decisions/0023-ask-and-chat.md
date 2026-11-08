# 0023: `chatcde ask` and `chatcde chat`: output, sources and data note

- Status: accepted
- Date: 2026-11-08

## Context
Issue #26 asks for the two free-question commands, with Rich output, citations as BORIS
links, and a note on the data edition and coverage. The agent (0022) returns an
`AgentResult`: the answer with `[BORIS:<id>, p. n]` references, citations resolved
against the catalogue, flags for records no tool returned, IDs not in the catalogue,
the stop reason, the tool calls and the provenance. Users run the open or the full
edition (0004), and how much full text is behind an answer differs between them.

## Decision
- **Answer:** rendered as Markdown. Every `[BORIS:…]` group becomes numbered
  references, numbered by first use (`[1, p. 4; 2]`); IDs not in the catalogue become
  `[?]`. Long UUIDs stay out of the text but remain in the sources and the JSON.
- **Sources** follow the answer, one line each: number, title, year, cited pages, the
  publisher DOI as a doi.org link, and the BORIS link. Links are clickable in terminals
  that support them.
- **Warnings** come after the sources:
  - a record that no tool returned in the conversation is marked "(not returned by any
    tool)";
  - cited IDs that are not in the catalogue are listed in red as unsupported;
  - a used-up tool budget or a cut-off answer is named.

  Nothing is silently dropped, so the reader can judge the answer.
- **Footer:** the model, prompt version, number of tool calls, time and tokens.
- **Data note:**
  - the edition comes from `manifest.json` in `data.dir`; a locally built catalogue
    counts as `full`, the same rule as Track A's `coverage.local_edition` (#45);
  - it gives the number of publications with full text (parsed pages, or the download
    flag before parsing), abstract only, and metadata only;
  - in the open edition it adds that restricted full texts are missing.

  `ask` prints the note after the answer, `chat` once at the start.
- **Progress:** while the model works, a spinner and one dim line per tool call (name,
  arguments, ok or error, seconds) go to stderr. `--hide-tools` turns the lines off.
  Stdout keeps only the answer, so `--json` output can be piped.
- **Options:**
  - `--json` prints the whole `AgentResult`;
  - `--log FILE` appends each answer as a JSON line, which builds material for
    `chatcde evaluate questions` (#28) and for checking answers later;
  - `--alternative` uses `model_alternatives.agent`.
- **`chat`:** one `Agent` per session, so follow-up questions see the earlier turns.
  `/reset` starts over, `/exit`, `/quit`, Ctrl-D and Ctrl-C leave. Empty lines are
  ignored. An endpoint failure is reported and the session goes on.
- **Exit codes:** 1 for no catalogue or a failed `ask`; 2 for `--alternative` without an
  alternative model in the config.

## Consequences
- Once #45 is merged, the data note could use `coverage_report()` instead of its own
  small query; the counting rule is the same.
- Not yet tried against GPUStack. Check on the VPN how the Markdown answers of gpt-oss
  look in the terminal, and whether long chats need their old tool messages trimmed
  (0022).
- Revisit: stream the answer token by token; a `/sources` command in chat to list every
  record seen so far; save a chat transcript as Markdown.
