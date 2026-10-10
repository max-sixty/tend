---
name: monitor-ci
description: Poll CI to a terminal result. Use after any push you are accountable for, and before calling a failure pre-existing.
metadata:
  internal: true
---

# Monitor CI

Obtain CI evidence for the caller's pinned commit. Return the result to the
calling workflow; that workflow decides what to repair or do next.

## Poll the pinned commit

Poll with the bundled script, pinned to the commit this session is accountable for — never the PR's current head: another actor can advance the head while the loop sleeps, and a poll that follows it reports *their* commit's results as yours:

```bash
# After your own push:
PINNED_SHA=$(git rev-parse HEAD)
# In a review session, HEAD is the ephemeral refs/pull/N/merge commit, which
# carries no rollup at all; pin the PR head instead:
#   PINNED_SHA=$(cat "$TMPDIR/reviewed-head")
# When the push happened in a $TMPDIR worktree the recipe then removes, capture
# the OID there — `git rev-parse HEAD > "$TMPDIR/<name>-sha"` — before the removal.
# Back in the main checkout HEAD is the default branch, not what you pushed.
uv run --script \
  "${CLAUDE_PLUGIN_ROOT}/scripts/poll_pr_checks.py" poll <number> "$PINNED_SHA"
```

Run this command in the foreground with the longest command timeout the harness allows. It has no fixed time limit: it waits until every check settles, however long the repo's CI takes. It returns sooner only when no check registers, or when checks still pend and the PR head moves.

The poll waits for every check, advisory ones included. Where the repo's overlay names checks the poll leaves out, pass each as `--skip '<check name>'`, the name `gh pr checks` shows: the poll neither waits for nor reads any check of that name, so a red one doesn't stop the verdict either.

Exit 0 is green, judged on the latest run of each check — where one workflow ran twice *independently* on the same SHA, read the earlier run's own conclusion before relying on it. Exit 1 is red, with the failing checks and their run URLs: diagnose each failure with `gh run view <run-id> --log-failed`. Any other exit or command timeout is **unverified, not green**. Exit 3 means the head moved: report the checks it lists as unverified, marking each required or advisory (`gh pr checks <number> --required` lists the required contexts already registered on the commit; an omnibus that hasn't registered yet is required too).

Before calling a failure pre-existing (**Grounded Analysis** in `/tend-ci-runner:run-tend`), check the recent default-branch runs of the workflow it belongs to. Filter by that workflow — on a bot-active repo an unfiltered listing fills with other workflows' runs.

```bash
DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')
# <workflow>: the workflow the failing check or test runs in, by name or file
gh run list --branch "$DEFAULT_BRANCH" --workflow "<workflow>" --status completed \
  --limit 5 --json conclusion,createdAt,url
```

If you cannot verify, say "I haven't confirmed whether these failures are pre-existing."

### Rerunning failed jobs

To rerun a run's failed jobs and wait for the outcome, use the bundled script — it reruns, finds the new attempt's jobs (the parent run's `.status` and the commit rollup stay pending on unrelated siblings, so neither is a usable signal), and polls them to terminal:

```bash
uv run --script \
  "${CLAUDE_PLUGIN_ROOT}/scripts/rerun_failed_jobs.py" <run-id>
```

Same foreground invocation as above; it waits until the re-run jobs finish. Exit 0 prints each job's conclusion — `completed` is not `success`; the follow-up turns on the conclusions. Any other exit means the rerun never took: report the jobs as unverified.
