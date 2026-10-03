# Promptfoo evals

Codex is the default executor and judge. Every attempt starts fresh; no original
actor history is resumed or added to the prompt. Run from the repo root with
Node 22.22+ and a local Codex subscription login:

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
  saved artifact, actual SDK tool events and the resulting repository diff.
  It evaluates the investigation and actions as well as the final prose.

Trajectory cases require their pinned Git commits to be available locally.
They retain repository evidence, not the original runner's installed
dependencies, processes or live GitHub state. Neither case type simulates
GitHub or recreates the original production session.

## Collector and case authoring

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

On 2026-10-02, validation with `gpt-6-sol` selected one completed fresh attempt
per arm after staging repairs and reruns, with zero execution errors:

| Cases | Historical | Current |
| --- | --- | --- |
| Six focused decisions | 5/6 | 6/6 |
| One repository trajectory | 0/1 | 0/1 |
| Total | 5/7 | 6/7 |

Both longer investigations missed the plugin shipment path, so the focused
added-fixture contrast did not transfer to repository review. The relocation
pair uses its actual saved artifacts regraded under the clarified public-review
versus internal-decision rubric. Calibration matched all five expected verdicts,
including rejection of public boilerplate and a correct artifact with no
investigation trace. These expected negative controls are grading failures,
not execution errors. The selected sample is in `.tmp/evals/fresh-validation.json`.

Deterministic preparation and provider checks run with:

```bash
uv run pytest evals/test_prepare.py
npm --prefix evals test
```

The optional Claude comparison supports focused cases only; preparation
explicitly excludes trajectory cases:

```bash
uv run python evals/prepare.py --harness claude
npm --prefix evals run eval -- --output ../.tmp/evals/claude-results.json
```
