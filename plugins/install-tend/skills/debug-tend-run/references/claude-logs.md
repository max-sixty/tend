# Parsing Claude session logs

Use these recipes when the artifact is `claude-session-logs*`. `$FILE` is
the JSONL path set in the skill's download step.

Each JSONL line has a top-level `type` field. The main message types are
`user` and `assistant` (with `.message.content`). Other types (`system`,
`progress`, `queue-operation`, `last-prompt`) carry metadata — ignore them
for most debugging.

## Overview — what happened

```bash
# Skills loaded
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use" and .name == "Skill") |
  .input.skill' "$FILE"

# Tool calls in order
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use") |
  "\(.name): \(.input | tostring | .[0:120])"' "$FILE"

# Assistant text (reasoning and responses)
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "text") | .text' "$FILE"
```

## Targeted queries

```bash
# What the bot was told (user messages including injected prompts)
jq -r 'select(.type == "user") |
  .message.content | if type == "string" then . else
  [.[]? | select(.type == "text") | .text] | join("\n") end' "$FILE"

# Bash commands executed
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use" and .name == "Bash") |
  .input.command' "$FILE"

# Tool results (what the bot saw back)
jq -r 'select(.type == "user") | .message.content[]? |
  select(.type == "tool_result") |
  "\(.tool_use_id): \(.content | tostring | .[0:200])"' "$FILE"

# Files read
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use" and .name == "Read") |
  .input.file_path' "$FILE"

# Files written or edited
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use" and (.name == "Write" or .name == "Edit")) |
  "\(.name): \(.input.file_path)"' "$FILE"

# GitHub API calls (gh commands, including inside variable assignments)
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use" and .name == "Bash") |
  .input.command | select(test("\\bgh\\b"))' "$FILE"
```

## Searching for specific behavior

```bash
# Find where the bot mentioned a keyword
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "text") | .text | select(test("KEYWORD"; "i"))' "$FILE"

# Find tool calls with specific input
jq -c 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use") |
  select(.input | tostring | test("KEYWORD"; "i"))' "$FILE"
```
