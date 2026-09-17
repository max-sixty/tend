<!-- Shared gates and evidence framework for review-reviewers and review-runs skills. -->
<!-- Symlinked into each skill directory; changes here apply to both. -->

## Confidence and magnitude gates

Before creating a PR, every finding must pass three gates.

### Gate 1: Confidence — is this a real problem?

| Evidence level | Meaning | Minimum occurrences to act |
|---|---|---|
| **Critical** | Clearly wrong outcome (closed wrong issue, merged broken code, deleted user data) | 1 |
| **High** | Consistent pattern across multiple sessions | 2–3 |
| **Medium** | Plausible problem seen once, could be noise | 5+ |
| **Low** | Nitpick or stylistic preference | Do not act |

Occurrences include both the current analysis **and** historical evidence recorded by prior runs. Each skill defines where that evidence lives — see the calling skill's "Evidence accumulation" section.

If a finding doesn't meet the threshold, **skip it** — don't create a PR, don't create an issue, don't comment. Record it in the evidence store so it can accumulate over future runs.

### Gate 2: Magnitude — is the fix proportionate?

| Change type | Examples | Evidence bar |
|---|---|---|
| **Removal / simplification** | Remove confusing sentence, delete dead guidance | Low (1 occurrence is enough) |
| **Targeted fix** | Fix a specific incorrect instruction, add a missing step | Normal (use Gate 1 thresholds) |
| **New paragraph or section** | Add explanation of a concept, new workflow guidance | High (need 3+ occurrences showing the gap) |
| **Structural change** | Reorganize a skill, add a new skill file, change workflow | Very high (need 5+ occurrences or a critical failure) |

**The larger the change, the more evidence required.** A one-line simplification needs less justification than a new paragraph. Prefer small, targeted fixes over broad rewrites.

### Gate 3: Cost — does the failure cost more than the fix?

Classify what the failure costs:

- **Wrong outward action** — a false-green verdict, a stale approval left standing, a wrong claim posted, an issue closed in error. Each occurrence does standing damage; real complexity is justified to prevent it.
- **Wasted compute** — a no-op session, a duplicated survey, a run lost to a blip that a later tick retries, a runner-hour burned by a slow or hung job. Each occurrence leaves no standing damage.

Classify by what the observed occurrence itself left on the public record. A hypothetical chain from waste to a wrong outward action ("the lost run could have left a stale approval standing") doesn't upgrade the class — the wrong action has to have occurred.

A waste-class failure supports only a fix that is itself nearly free, and only once the waste has recurred on separate days: one existing knob in one place, removed machinery, or a one-line condition. Judge the whole change, so repeated settings across workflows, jobs, platforms, or call sites count as one configuration scheme. One that needs new mechanism — a retry framework, another skip-gate, scheduling arithmetic, a cache — fails this gate at any occurrence count; a mechanism compressed into one dense line is still a mechanism, so judge by what the fix leaves behind (logic a future session must re-derive, a rule every later run loads, failure modes of its own), not its line count. Record the waste in the evidence store with its cost; if the aggregate grows to matter, escalate the number to the maintainer, who owns the simple levers (cadence, disabling a workflow).

### Structural vs. stochastic failures

Before applying the gates, classify each failure by asking: **did the bot have a decision point?**

- **Structural**: no decision point — the same conditions produce the same failure every time, regardless of how the bot approached the task. E.g., "the checkout differs between `pull_request_target` and `issue_comment` events, so grepping always finds stale content." Structural classification raises confidence the failure will recur, but does **not** override Gate 1 — a non-Critical structural failure still needs the occurrence count its evidence level requires (High = 2–3, Medium = 5+). Only **Critical** structural failures act on a single occurrence.

- **Stochastic**: the failure is a probabilistic model behavior — e.g., "the model was too agreeable when challenged" or "the model forgot to check X." The same model might handle the next identical situation correctly without any guidance change. These need significantly more evidence (5+ occurrences) because adding guidance for a one-off stochastic lapse adds noise that can degrade performance on other tasks. The 5+ floor governs a stochastic failure whatever evidence level its entries carry — no reclassification, pre-registered condition, or escalation recorded in the evidence store lowers it.

The test: "If I replayed this exact scenario 10 times, would the failure occur every time (structural) or only sometimes (stochastic)?" When in doubt, classify as stochastic and wait for more evidence.

### Applying the gates

For each finding, state:
1. The evidence level and occurrence count (current + historical)
2. Whether the failure is structural or stochastic
3. The failure's cost class (wrong outward action / wasted compute)
4. The proposed change type
5. Whether it passes all three gates

Only proceed to act on findings that pass all three gates.

### Non-issues: do not flag these

Some patterns look suspicious but are intentional — flagging expected behavior creates maintainer churn and costs trust. Three structural rules cover them:

- **Designed no-ops.** Many events correctly end with nothing posted, at whatever layer catches them: a pre-boot gate skip (`tend-mention`'s verify gate on the bot's own comments and reviews — though targets on older pinned releases still boot sessions for those), or a session that boots and exits silently (`tend-triage` on the bot's own monthly tracking-issue creation; the `issue_comment.edited` retrigger after a commenter refines their comment — the edit can change relevance, so the retrigger must re-evaluate; `tend-notifications` mark-reading a cross-repo `ci_activity` notification from an abandoned fork). These cost compute, not correctness — Gate 3 classifies them waste-class: record and move on; do not propose a skip-gate, label filter, pre-check, or occurrence threshold to save the boot. A loop that produces *wrong outward actions* (duplicate comments, spurious reviews) is different — that passes Gate 3, and a label-based skip is preferred over an authorship filter where a label can express it.

- **Designed silence.** The bundled `/tend-ci-runner:review` skill authorizes posting nothing when there is nothing actionable: on a self-authored PR (GitHub rejects self-approvals, so APPROVE isn't an option), on a draft PR (COMMENT-only mode; GitHub blocks approving drafts), or when the PR closed or merged while its run was queued. GitHub reports drafts as `state: OPEN`, so before reading a missing review as omission — or escalating to session logs to explain it — check `gh api repos/OWNER/REPO/pulls/N --jq '{state, draft}'` and the PR's literal author (`gh pr view <n> --json author --jq '.author.login'`; owner-authored PRs are approved normally and are no bot-authored-APPROVE precedent).

- **The reviewer role is independent of authorship.** `tend-review` re-reviewing — and re-approving — after any tend workflow pushes a fix commit is the design, not a re-approval loop; authorship-keyed guards that skip re-review drop real work and are not an accepted shape. Stacked approvals from racing runs are a *concurrency* artifact (cancelled runs POSTing before the SIGTERM arrived), not a review-rule problem.

**A merged fix still reproduces where the bot runs.** Every repo calls a pinned action ref, so a skill fix that merged in `max-sixty/tend` stays dormant until the next release tags. Observing the bug is therefore not evidence the fix is missing — check tend's merged PRs before filing, or the report is churn on something already landed.

## Finding format

Each run appends findings to the skill's evidence store under a `## Run <run-id>` heading. **Always derive the run ID, timestamp, and repo from the CI environment — never hand-type them.** Past sessions have filled the `<run-id>` placeholder with fabricated round numbers (e.g. `24294000000`) when the skill didn't explicitly point at `$GITHUB_RUN_ID`, producing dead link-anchors in the evidence log.

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

Each run gets its own heading so future runs can count prior occurrences and trace incidents to session logs.

**Name people without `@`.** Write `` `alice` ``, not `@alice`. The evidence store is an internal accounting log nobody asked to be in; every `@handle` in it — including one added by appending to an existing comment — notifies that person and auto-subscribes them to every later entry. Backticks keep the attribution and drop the ping. Same rule for a PR or issue body opened from a finding, and it holds even when the person is a collaborator of the repo you're writing in.

When a historical entry looks like it might match a current finding, **download and investigate the linked workflow's session logs** — don't rely on the summary text alone, which lacks sufficient context to judge relatedness. Trace the original decision chain in the session JSONL to confirm the historical case is genuinely the same pattern, not just superficially similar.
