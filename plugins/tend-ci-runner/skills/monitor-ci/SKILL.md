---
name: monitor-ci
description: Poll CI to a terminal result. Use after any push you are accountable for, and before calling a failure pre-existing.
metadata:
  internal: true
---

# Monitoring CI after a push

After pushing, what to do depends on whether a red result creates a follow-up.

**A pushed fix is always gated** (triage fix, CI fix, requested change): you own its CI, so don't pre-judge a fresh push as ungated — no other tend run fixes a PR branch's CI (`tend-ci-fix` watches only the default branch). Approving a PR is also gated: dismiss it on red.

**Nothing gated** (review-only, a reply, a no-op): end, stating anything still in flight. Don't background-poll — the completion notification isn't reliably delivered to a CI session.

Poll with the bundled script, pinned to the commit this session is accountable for — never the PR's current head: another actor can advance the head while the loop sleeps, and a poll that follows it reports *their* commit's results as yours:

```bash
# After your own push:
PINNED_SHA=$(git rev-parse HEAD)
# In a review session, HEAD is the ephemeral refs/pull/N/merge commit, which
# carries no rollup at all; pin the PR head instead:
#   PINNED_SHA=$(gh pr view <number> --json headRefOid --jq '.headRefOid')
# When the push happened in a $TMPDIR worktree the recipe then removes, capture
# the OID there — `git rev-parse HEAD > "$TMPDIR/<name>-sha"` — before the removal.
# Back in the main checkout HEAD is the default branch, not what you pushed.
uv run --script \
  "${CLAUDE_PLUGIN_ROOT}/scripts/poll_pr_checks.py" poll <number> "$PINNED_SHA"
```

Run this command in the foreground with a 10-minute command timeout.

Exit 0 is green, judged on the latest run of each check — where one workflow ran twice *independently* on the same SHA, read the earlier run's own conclusion before relying on it. Exit 1 is red, with the failing checks and their run URLs: diagnose with `gh run view <run-id> --log-failed`, fix, commit, push, and poll the new commit. Any other exit or command timeout is **unverified, not green**. The cap is the whole poll budget — the pending count includes advisory jobs (an hourly benchmark matrix never reaches zero), so don't re-enter the loop; report the still-pending checks as unverified, marking each required or advisory (`gh pr checks <number> --required` lists the required contexts already registered on the commit; an omnibus that hasn't registered yet is required too).

When the system prompt says the merge mode is `yolo`, exit 0 is the merge gate. Re-read the PR and require it to be open with `headRefOid == PINNED_SHA`, then merge through the pull-request REST endpoint with that SHA. Never use auto-merge, never omit `sha`, and leave the PR open if the head moved or GitHub refuses the merge:

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
PR_STATE=$(gh pr view <number> --json state,headRefOid)
test "$(jq -r .state <<<"$PR_STATE")" = OPEN
test "$(jq -r .headRefOid <<<"$PR_STATE")" = "$PINNED_SHA"
gh api "repos/$REPO/pulls/<number>/merge" -X PUT \
  -f sha="$PINNED_SHA" -f merge_method=squash
```

Before calling a failure pre-existing (**Grounded Analysis** in `/tend-ci-runner:run-tend`), check the recent default-branch runs of the workflow it belongs to. Filter by that workflow — on a bot-active repo an unfiltered listing fills with other workflows' runs.

```bash
DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')
# <workflow>: the workflow the failing check or test runs in, by name or file
gh run list --branch "$DEFAULT_BRANCH" --workflow "<workflow>" --status completed \
  --limit 5 --json conclusion,createdAt,url
```

If you cannot verify, say "I haven't confirmed whether these failures are pre-existing."

### A review that lands while you poll is not yours to action

`tend-review` fires on any PR you open, so its review often arrives while you are still polling that PR's checks. Don't act on it. That review session applies the findings it raised itself, so a session that starts editing is racing a run already making the same edits and running the same suite. The loser only finds out at `git push`, discards its commit, and the whole fix-and-verify cycle is paid twice for one review.

Poll your checks to terminal, do the follow-up you were gated on, and exit; name the outstanding review in your summary. This covers a review that arrives *while* you work — a session dispatched to answer a specific review owns that review and actions it normally.

**On a fork PR the premise fails — nothing succeeds you.** The review session applies its own findings only where the PR has no human author, and a fork PR is the contributor's — so the review posts them and stops. The notifications poll can't pick them up either: GitHub doesn't notify an actor of their own activity, so the bot's own review is invisible there by construction. Findings left for a successor session strand until a human happens to comment. So if you pushed the commits under a maintainer directive you are the de-facto author — action your own review's findings before ending. If you pushed them without one, name them in your closing comment as unaddressed and unowned, so the thread shows someone has to pick them up. A review on commits the contributor pushed already reached them — leave it.

### Rerunning failed jobs

To rerun a run's failed jobs and wait for the outcome, use the bundled script — it reruns, finds the new attempt's jobs (the parent run's `.status` and the commit rollup stay pending on unrelated siblings, so neither is a usable signal), and polls them to terminal:

```bash
uv run --script \
  "${CLAUDE_PLUGIN_ROOT}/scripts/rerun_failed_jobs.py" <run-id>
```

Same foreground invocation and 10-min `timeout` as above. Exit 0 prints each job's conclusion — `completed` is not `success`; the follow-up turns on the conclusions. Any other exit means the rerun never took or the jobs are still running at the cap: report them as unverified rather than re-entering.
