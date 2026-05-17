# Parsing Codex session logs

Use these recipes when the artifact is `codex-session-logs-*`. `$FILE` is
the rollout JSONL path set in the skill's download step.

Each JSONL line has a top-level `type` of `session_meta`, `turn_context`,
`event_msg`, or `response_item`. Substantive content sits under
`response_item.payload`, with the variant in `.payload.type`:

- `message` — initial input from `user` or `developer` (system prompt,
  AGENTS.md, skill listings); text at `.payload.content[].input_text.text`
- `agent_message` — text emitted by the model during the turn
  (`.payload.message`)
- `function_call` — tool invocation; `.payload.name` plus
  `.payload.arguments` (a JSON-encoded string, parse with `fromjson`)
- `function_call_output` — paired result; `.payload.call_id` and
  `.payload.output`
- `reasoning` — opaque encrypted blob; skip
- `task_started`, `task_complete`, `token_count` — metadata;
  `task_complete.last_agent_message` carries the final reply

The bot drives shell through one tool, `exec_command`, whose arguments
parse to `{cmd, workdir, yield_time_ms, max_output_tokens}`. Long-running
commands also produce `write_stdin` calls. Codex has no dedicated
Read/Write/Edit tool; file I/O appears as `cat`, `sed`, `tee`,
`apply_patch`, etc. inside `exec_command`.

## Overview — what happened

```bash
# Skills loaded (Codex reads SKILL.md via shell rather than a dedicated tool)
jq -r 'select(.payload.type == "function_call" and .payload.name == "exec_command") |
  .payload.arguments | fromjson | .cmd | select(test("SKILL\\.md"))' "$FILE"

# Final summary the bot returned
jq -r 'select(.payload.type == "task_complete") | .payload.last_agent_message' "$FILE"

# Tool calls in order
jq -r 'select(.payload.type == "function_call") |
  "\(.payload.name): \(.payload.arguments | fromjson | (.cmd // tostring) | .[0:160])"' "$FILE"

# Interim model narrative (its visible reasoning; the encrypted blob is opaque)
jq -r 'select(.payload.type == "agent_message") | .payload.message' "$FILE"
```

## Targeted queries

```bash
# All shell commands
jq -r 'select(.payload.type == "function_call" and .payload.name == "exec_command") |
  .payload.arguments | fromjson | .cmd' "$FILE"

# Pair each command with its (truncated) output. Parens are required:
# jq binds `,` tighter than `|`, so `A | B, C | D` is `A | (B, C) | D`.
jq -r '(select(.payload.type == "function_call" and .payload.name == "exec_command")
        | "→ " + (.payload.arguments | fromjson | .cmd)),
       (select(.payload.type == "function_call_output")
        | "← " + (.payload.output | .[0:300]))' "$FILE"

# gh CLI calls (including in variable assignments)
jq -r 'select(.payload.type == "function_call" and .payload.name == "exec_command") |
  .payload.arguments | fromjson | .cmd | select(test("\\bgh\\b"))' "$FILE"

# File writes / edits (apply_patch, tee, sed -i, redirect to absolute path)
jq -r 'select(.payload.type == "function_call" and .payload.name == "exec_command") |
  .payload.arguments | fromjson | .cmd |
  select(test("apply_patch|\\btee\\b|sed -i\\b|>\\s+/"))' "$FILE"

# Initial prompts (AGENTS.md, skill list, triggering event description)
jq -r 'select(.payload.type == "message" and (.payload.role == "user" or .payload.role == "developer")) |
  .payload.content[]? | select(.type == "input_text") | .text' "$FILE"
```

## Searching for specific behavior

```bash
# Model text mentioning a keyword
jq -r 'select(.payload.type == "agent_message") | .payload.message |
  select(test("KEYWORD"; "i"))' "$FILE"

# Commands mentioning a keyword
jq -r 'select(.payload.type == "function_call" and .payload.name == "exec_command") |
  .payload.arguments | fromjson | .cmd | select(test("KEYWORD"; "i"))' "$FILE"
```
