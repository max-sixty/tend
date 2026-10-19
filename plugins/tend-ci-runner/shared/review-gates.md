<!-- Evidence framework for the review-runs skill, and for tend's own -->
<!-- review-reviewers overlay. Symlinked into each; changes here apply to both. -->
<!-- It ships with the plugin: every rule here has to hold in a consumer's repo. -->

## Evidence and remedy

Act on findings whose evidence establishes a problem and supports a repair.
Read the current and historical observations in the calling skill's evidence
store, verifying related cases from their session logs. Recurrence shows
exposure; a count alone establishes neither a shared cause nor a suitable fix.

Judge confidence from the mechanism: reproduction, the code or instructions
that produce it, and the observed outcome. Distinguish a wrong outward action
from a hypothetical consequence. A deterministic defect need not wait for
another run to fail before it is repaired.

Model behavior can vary even under adequate instructions. Use
`/tend-ci-runner:propose-instructions` to judge whether an instruction repair
is warranted.

Choose the repair by the cause and consequences, including the risk and upkeep
it introduces. Remove or simplify where that resolves the problem; complete a
broader repair where the cause crosses boundaries. Text length and file count
do not establish either confidence or proportionality. Record unresolved
observations for later investigation rather than proposing a speculative fix.

For each finding, explain the observed outcome, causal evidence, remaining
uncertainty, and why the proposed remedy fits. Prioritize by impact and
confidence, and group related repairs by theme.

### Non-issues: do not flag these

Some patterns look suspicious but are intentional — flagging expected behavior creates maintainer churn and costs trust. Three structural rules cover them:

- **Designed no-ops.** Many events correctly end with nothing posted, and the layer that catches one varies: a pre-boot gate skips the job, or a session boots, judges the trigger, and exits silently. Both are the design — a workflow fires on an event class, and most of a class needs no response. Record them and move on. A loop that produces *wrong outward actions* — duplicate comments, spurious reviews — is a finding, and where a label can express the distinction a label-based skip beats an authorship filter.

- **Designed silence.** `/tend-ci-runner:review`'s `references/draft-mode.md` and the skill itself are the contract for when a review posts nothing, so read them before calling a missing review an omission. Two states the listing hides: GitHub reports a draft as `state: OPEN` (`gh api repos/OWNER/REPO/pulls/N --jq '{state, draft}'`), and the PR's literal author decides whether an approval was available at all (`gh pr view <n> --json author --jq '.author.login'`) — a human owner's PR is approved normally and is no precedent for approving a bot-authored one.

- **The reviewer role is independent of authorship.** `tend-review` re-reviewing — and re-approving — after any tend workflow pushes a fix commit is the design, not a re-approval loop; authorship-keyed guards that skip re-review drop real work. Stacked approvals from racing runs are a *concurrency* artifact (cancelled runs POSTing before the SIGTERM arrived), not a review-rule problem.

## Finding format

Each run appends findings to the skill's evidence store under a `## Run <run-id>` heading. **Always derive the run ID, timestamp, and repo from the CI environment — never hand-type them.**

```bash
RUN_ID="$GITHUB_RUN_ID"
TIMESTAMP=$(date -u -Iseconds | sed 's/+00:00/Z/')
REPO="$GITHUB_REPOSITORY"
```

When composing the findings file, either interpolate the values with an unquoted heredoc (so `$RUN_ID` expands) or read them first and write the literal values into the file:

```
## Run <RUN_ID> — <TIMESTAMP>

### <short description>
- **Evidence level**: Medium
- **Occurrences this run**: 1
- **Run ID**: <RUN_ID>
- **Workflow**: https://github.com/<REPO>/actions/runs/<RUN_ID>
- **Session**: <session file>
- **Detail**: <brief description of what was observed>
```

Each run gets its own heading so future runs can trace related observations and their causes to session logs.

**Name people without `@`.** Write `` `alice` ``, not `@alice`. The evidence store is an internal accounting log nobody asked to be in; every `@handle` in it — including one added by appending to an existing comment — notifies that person and auto-subscribes them to every later entry. Backticks keep the attribution and drop the ping. Same rule for a PR or issue body opened from a finding, and it holds even when the person is a collaborator of the repo you're writing in.

When a historical entry looks like it might match a current finding, **download and investigate the linked workflow's session logs** — don't rely on the summary text alone, which lacks sufficient context to judge relatedness. Trace the original decision chain in the session JSONL to confirm the historical case is genuinely the same pattern, not just superficially similar.
