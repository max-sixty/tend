# Promptfoo evals

Run from the repo root with Node 22.22+, authenticated Claude Code and `gh`:

```bash
npm --prefix evals ci --ignore-scripts
uv run python evals/prepare.py
npm --prefix evals run eval -- --output ../.tmp/evals/results.json
```

The command compares historical and current guidance, three attempts each.
Historical failures are expected; they make Promptfoo exit nonzero. Inspect the
JSON's per-provider results: execution errors are not behavioral failures.
To narrow a run, append `--filter-pattern Worktrunk` or `Leaf`.
Use `--filter-providers current` to check only current guidance.

Promptfoo's built-in Claude Agent SDK provider forks the prepared history for
each attempt. It runs in an empty temporary directory with Read, Skill and Write,
no discovered settings or MCP servers, and reads confined to that directory and
its staged plugin. Existing Claude authentication is used without copying
credentials. Write is permitted only for `captured.md`. A shared transform
extracts its last completed Write from the SDK trace; a missing or failed write
is an execution error. Inline `llm-rubric` assertions grade the literal body
against prose criteria, using Claude Sonnet 5 through the same SDK provider and
local login. Grading adds model calls and can vary between runs.

Preparation replaces staged plugins; never run it during an eval. These cases
resume the drafting decision, not repository edits or live GitHub actions.
A no-plugin comparison would still contain previously loaded skills in history.
Deterministic checks run with:

```bash
uv run pytest evals/test_prepare.py
npm --prefix evals test
```

## Add a case from history

Start with an issue/PR URL, a time with its time zone, and the behavior to check.
Use `/install-tend:debug-tend-run` to inspect the original Claude session.
Thread artifacts are named `claude-session-logs-n<NUMBER>`; scheduled runs use
invocation IDs, so also inspect runs around the supplied time.

Keep only `cases/<name>/source.json` and `case.yaml` in Git:

- `source.json` pins the repository, run, artifact, transcript path and SHA-256,
  historical Tend ref, original model, bot, injected skills, and an exclusive
  `before` timestamp. Choose a cutoff immediately before the decision, with
  completed tool calls and no original bad answer. Recover the actual appended
  system prompt; pin its hash if it differs from the historical shared prompt.
- `case.yaml` is a Promptfoo test: `description`, `vars.task` and `assert`.
  Ask for the next artifact using retained evidence and a Write to `captured.md`;
  keep expected behavior in prose assertions, out of the task, for example:

  ```yaml
  assert:
    - type: llm-rubric
      value: The description references the partially addressed issue without closing it.
  ```

  File references
  resolve relative to `.tmp/evals/prepared/promptfooconfig.yaml`.
  Prepared configuration pairs the test only with its own historical/current
  providers.

Preparation downloads and verifies the source, cuts history, and stages both
plugins. It replaces injected Tend skill bodies in the current arm, retaining
investigation and consumer guidance. Confirm historical runs reproduce the
failure before claiming an improvement. Small samples establish reproduction,
not rates. Calibrate new graders against known bad and good artifacts.

Sources are cached by transcript hash under `~/.local/share/tend/evals/sources/`,
outside the repo and across worktrees. GitHub artifacts expire: a fresh machine
needs a surviving artifact or a copy of that cache. A URL and time cannot
recover expired logs or reconstruct edited GitHub state. This is an agent
case-authoring recipe; the downloader itself is deterministic.

## Prototype status

The Promptfoo cutover reproduced both historical failures in 3/3 attempts each.
Current guidance passed partial-close 3/3 and wrapping 2/3: one current draft
still hard-wrapped prose. These counts establish reproduction, not error rates.
The prose criteria matched all 19 known verdicts in a calibration: the 12 saved
drafts, the original bad wrapping, two valid formatting controls, and four
issue-reference edge cases. This is a single judge pass, not a reliability rate.
The file-writing step matters; returning a final answer instead failed to
reproduce wrapping in the first comparison.

Future sessions may need to refine cutoff selection, prompt recovery and skill
replacement for new session formats. Only injected guidance is replaced;
instruction source and diffs read through tools remain evidence. Inspect those
for answer leakage or conflicting historical guidance before trusting a case.
Promptfoo also has a Codex SDK provider, but Codex cannot natively resume these
Claude histories. A Codex case needs its own evidence and failing control.
