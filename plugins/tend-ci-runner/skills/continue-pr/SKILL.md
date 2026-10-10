---
name: continue-pr
description: Continue the configured bot's unfinished PR, including red CI and unresolved findings after a prior response or review.
argument-hint: "[PR number]"
metadata:
  internal: true
---

# Continue a Bot PR

Finish the repair on its existing branch, using the live PR as the work record.
Read its diagnosis, attempted fixes, review findings, and current checks before
deciding what remains. A response or review can be complete while the repair is
still unfinished.

## Required skills

- Load `/tend-ci-runner:run-tend` and its repo-specific overlay first.
- Load `/tend-ci-runner:fix-a-bug` before fixing, `/tend-ci-runner:push-commits`
  before pushing or withdrawing the PR, `/tend-ci-runner:monitor-ci` to verify
  the head, and `/tend-ci-runner:merge-pr` for its landing decision.
- Load `/tend-ci-runner:resolve-conflicts` for conflicts and
  `/tend-ci-runner:post-to-github` before updating the thread.

## Establish ownership

```bash
BOT_LOGIN=$(gh api user --jq .login)
gh pr view <number> --json author,state,isDraft,headRefOid,headRefName,headRepository,baseRefName,commits,body,comments,reviews,statusCheckRollup
uv run --script \
  "${CLAUDE_PLUGIN_ROOT}/scripts/active_subject_runs.py" \
  "https://api.github.com/repos/$GITHUB_REPOSITORY/pulls/<number>"
```

Continue only an open PR authored by the configured bot with its head in this
repository. Establish ownership per **Subject-run ownership**
in `/tend-ci-runner:merge-pr`; defer to an independent dedicated owner. A human
takeover or maintainer instruction to stop leaves the work with them. Read any
related in-flight PR before treating it as the owner of this repair: an
investigation or partial fix can leave work here.

## Complete the remaining work

Read inline review comments too (`gh api
"repos/$GITHUB_REPOSITORY/pulls/<number>/comments" --paginate`). Reproduce the
remaining failure on the live head and continue diagnosis from the prior
evidence. Apply a safe fix, verify it, and push to this PR's branch. Use an
isolated `$TMPDIR` worktree when the caller is also handling other subjects;
retain the original checkout for its remaining work.

A previous failed attempt or uncertain diagnosis calls for further diagnostic
work; it does not by itself transfer the repair to a maintainer. Stop for a
current blocker you cannot clear, such as inaccessible evidence or tooling, an
external dependency, or a semantic decision with no defensible default. State
what is blocked and what would unblock it. Re-check a recorded blocker against
current state before relying on it.

When prior attempts failed to clear the same failure, another push needs new
evidence or a materially different approach. Continue diagnosis; if neither
emerges, record what evidence is missing instead of repeating the attempted
fix. Each push starts another review round.

Re-check head and state before expensive verification and before pushing per
**Re-check the head SHA before the expensive verify, not just before the push**
in `/tend-ci-runner:push-commits`. Preserve any sibling's commits. A review that
arrives while this run works remains with its reviewer per
**Reviews arriving during verification** in `/tend-ci-runner:push-commits`.

Once the remaining findings are addressed, poll the pinned head per
`/tend-ci-runner:monitor-ci`, including a head this run did not push. Handle
failures as remaining repair work, then complete the landing decision per
`/tend-ci-runner:merge-pr`. If the repair is obsolete or its premise
is disproved, withdraw only the bot's PR after the branch-state recheck in
`/tend-ci-runner:push-commits`.

Update the PR's diagnosis and verification when the work changes them. An
unchanged blocker needs no repeat comment. A deduplicated response or review
suppresses that post, not the remaining repair work.
