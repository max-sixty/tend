# Promptfoo evals

Codex is the executor and judge. Model settings live in `prepare.py`;
`codex-provider.cjs` sets the reasoning effort. Every attempt starts fresh,
without original actor history. Run from the repo root with Node 22.22+ and a
local Codex subscription login:

```bash
npm --prefix evals ci --ignore-scripts
uv run python evals/prepare.py
npm --prefix evals run eval -- --output ../.tmp/evals/results.json
```

The suite compares historical and current plugin guidance, three attempts per
arm, with caching disabled. Both arms use the same current shared system prompt
and model. Preparation replaces staged inputs; do not run it during an eval.
Append `--filter-pattern "[Gg]allery"` to narrow the suite or
`--filter-providers current` to run only current guidance. Failed assertions
make Promptfoo exit nonzero; inspect individual results to distinguish them
from execution errors.

Promptfoo's table has a column per arm (`codex/historical`, `codex/current`)
and a row per case, and every case runs on both arms. Each test's metadata
names its case and kind; the provider reads that case's staged inputs from its
arm. Promptfoo records every run in its local database under a description
naming `git describe` of the current arm, and `npm --prefix evals run view`
opens its viewer on them.

Each Codex attempt gets an isolated workspace and HOME/CODEX_HOME with
subscription authentication and a controlled permission profile. Personal
settings, plugins and MCP servers are absent. Commands can inspect staged files
and minimal runtime paths; network access is disabled. The provider reads the
actual `captured.md` bytes, and a missing artifact is an execution error. The
judge runs separately without the executor's skills or workspace.

## Case types

- **Focused:** a concise state brief and hash-pinned evidence files let a fresh
  agent make the next decision. The grader evaluates its saved artifact.
- **Trajectory:** a real Git snapshot under `repository/` and frozen event
  observations let the agent investigate the change. The grader receives the
  saved artifact, actual SDK tool events and the independently observed repository
  diff, status and initial/final commit identities.
  It evaluates the investigation and actions as well as the final prose. Read-only
  review cases require a nonblank artifact, an actual recorded command and
  unchanged independently observed repository state. These deterministic checks
  establish evidence presence and state integrity; the model judge assesses
  relevant investigation, supported claims and other tool actions.

Trajectory cases require their pinned Git commits to be available locally.
Preparation fetches their ancestry so merge-base and normal three-dot PR diffs
work even on longer branches.
They retain repository evidence, not the original runner's installed
dependencies, processes or live GitHub state. Neither case type simulates
GitHub or recreates the original production session.

## Collector and case authoring

Keep this runbook to current execution, authoring and validation guidance.
Case-specific expectations belong in `case.yaml`, provenance in `source.json`,
and results and experiment history in run artifacts.

An agent chooses the historical event, starting brief, evidence and grading
criteria. Preparation deterministically verifies file hashes, stages the
pinned inputs and repository refs, and installs each arm's plugin:

```text
past event → fresh brief + pinned files/refs → isolated attempt
           → artifact + current trajectory → grading
```

Keep `cases/<name>/case.yaml`, `source.json` and the frozen evidence in Git.
`case.yaml` supplies `vars.task` and `assert`; ask for the next artifact in
`captured.md`. Keep expected behavior in assertions rather than the task or
evidence-selection hints.

`source.json` records `kind: focused | trajectory`, `historical_ref`, `bot`,
`merge`, and a `fixtures` mapping from case-relative paths to SHA-256 hashes.
For a trajectory, `checkout` pins `base` and `head`, and may pin
`previous_review_head` for an incremental review. The base and optional previous
review head become local Git refs; the pinned head is checked out as detached HEAD.

Keep original run, artifact, transcript, model and timestamps under `origin`
for audit. Preparation does not read or download that origin history. See
`cases/draft-review-trajectory/` for a real draft-review investigation and
`cases/partial-close/` for a focused decision.

A hash proves identity, not representativeness. A brief can omit the original
search, conflicting observations or opaque context, and can make decisive
evidence easier to find. Check chronology, omissions and answer leakage before
trusting a case. A trajectory preserves more investigation opportunity while
still lacking live GitHub and the original runner environment.

## Validation

Require a failing historical control on the selected executor before claiming
an instruction improvement. Both arms passing provides coverage, not evidence
of improvement. Calibrate changed rubrics on known good and bad artifacts or
trajectories, and inspect saved drafts and tool events alongside verdicts.
Small samples establish observed behavior rather than reliability rates.

Compare arms with identical starting evidence, model and execution settings.
Confirm from the execution trace that each arm loaded its own edited guidance.
When grading criteria change, regrade both arms with the same criteria before
comparing results. A grading correction alone is not an instruction improvement.

Deterministic preparation and provider checks run with:

```bash
uv run pytest evals/test_prepare.py
npm --prefix evals test
```
