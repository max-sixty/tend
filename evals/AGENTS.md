# Native Claude evals

Run from the repo root with authenticated `claude` and `gh`:

```bash
uv run python evals/prepare.py
claude plugin eval .tmp/evals/prepared/current/tend-ci-runner \
  --ablation none --runs 3 --allow-tools Write --judge-model sonnet \
  --keep-temp --no-publish --trust-plugin --output-dir .tmp/evals/native/current
```

For the historical control, replace `current` with `historical` in both paths.
Compare those arms: a no-plugin ablation still contains previously loaded skills
in the retained history.
Use `--case wrapping` to narrow a run; `--model haiku` changes the executor,
independently of `--judge-model`. Inspect failures in `aggregate-result.json`
and the HTML report: execution errors are not behavioral failures. Wrapping uses
a model judge; calibrate changed graders against known good and bad artifacts.

Preparation replaces staged plugins; never run it during an eval. These cases
resume the drafting decision, not repository edits or live GitHub actions.

## Add a case from history

Start with an issue/PR URL, a time with its time zone, and the behavior to check.
Use `/install-tend:debug-tend-run` to locate and inspect the original Claude
session. Thread-triggered artifacts are named `claude-session-logs-n<NUMBER>`;
scheduled runs use invocation IDs, so also inspect runs around the supplied time.

Keep only `cases/<name>/source.json` and `case.yaml` in Git:

- `source.json` pins the repository, run, artifact, transcript path and SHA-256,
  historical Tend ref, bot, injected skill names, and an exclusive `before`
  timestamp. Inspect the session to choose a cutoff immediately before the
  decision being tested, with completed tool calls and no original bad answer.
  Recover the actual append-system prompt; pin its hash if it differs from the
  historical shared prompt. See the existing manifests for the format.
- `case.yaml` names `context.history_file: history.jsonl`, the original model,
  continuation prompt and graders. Ask for the next artifact using retained
  evidence; keep the expected behavior in the grader, out of the prompt.

Preparation downloads and verifies the source, cuts the history at `before`,
and stages historical/current plugins. It replaces injected Tend skill bodies
in the current arm while retaining the investigation and consumer guidance.
Check both arms: historical runs should reproduce the failure before using the
case to claim an improvement. Small samples establish reproduction, not rates.

Raw histories and recovered prompts are cached by transcript hash under
`~/.local/share/tend/evals/sources/`, outside the repo and across worktrees.
GitHub artifacts expire: a fresh machine needs a surviving artifact or a copy
of that cache. A URL and time cannot recover expired logs or reconstruct edited
GitHub state. This is an agent authoring recipe, not an automatic importer.

## Prototype status

Preparation was exercised on three production histories: the two regression
cases and a Tend PR-description capture probe. Future sessions may need to
refine cutoff selection, prompt recovery and skill replacement for new session
formats. Only injected execution guidance is replaced; instruction source and
diffs read through tools remain evidence. Inspect those for answer leakage or
conflicting historical guidance before trusting a new comparison.

## Fixture-only reconstructions

`ci-failure-attribution/` keeps a native Claude case, provenance and committed
evidence outside the installed plugin. Its [README](ci-failure-attribution/README.md)
owns the temporary-plugin staging recipe. Run it separately; `prepare.py`
handles transcript cases only.
