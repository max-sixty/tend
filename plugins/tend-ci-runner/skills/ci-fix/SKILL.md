---
name: ci-fix
description: Debug failing CI on the default branch. Use when CI fails on main.
argument-hint: "[run-id and context]"
metadata:
  internal: true
---

# Fix CI on Default Branch

CI failed on the default branch. Diagnose the failure, fix any durable cause,
and create a PR when code or configuration needs to change.

**Run:** $ARGUMENTS

## Workflow

### 0. Load required skills

- Load `/tend-ci-runner:run-tend` first, including its repo-specific overlay.
- Load `/tend-ci-runner:ground-claims` before reading and matching the current failure.
- Load `/tend-ci-runner:fix-a-bug` before attempting a fix.
- Load `/tend-ci-runner:post-to-github` before composing a comment, issue, or PR body.
- Load `/tend-ci-runner:open-pr` before opening an issue or PR.
- Load `/tend-ci-runner:push-commits` before pushing a fix and `/tend-ci-runner:monitor-ci` after it.

### 1. Read the current failure

1. Read the run's conclusion and jobs: `gh run view <run-id> --json conclusion,jobs,url`
2. Read its logs: `gh run view <run-id> --log-failed`. If the output is truncated or only lists failed tests, save it and read the deciding diagnostic before matching prior work.

Establish the current failure per **Claims of recurrence** in
`/tend-ci-runner:ground-claims` before reading earlier diagnoses.

### 2. Check for existing fixes

Check whether any PR already addresses the same failure — a prior bot attempt, a prior bot fix a maintainer rejected, or a maintainer's in-flight fix under any branch name. List the open ones; search the closed ones, since a recency listing drops an older rejection past any `--limit`:

```bash
gh pr list --state open --limit 200 --json number,title,state
# <shape>: the failing test or job name, or a distinctive phrase from the error;
# search matches titles and bodies, so run one per term
gh pr list --state closed --search "<shape>" --limit 100 --json number,title,state
```

Match the current diagnostic evidence against the candidate PR's diagnosis and diff, per **Claims of recurrence** in `/tend-ci-runner:ground-claims`. Branch names encode run IDs and never repeat. The listing carries titles only; pull the body and author per candidate (`gh pr view <n> --json body,author`), then read its diff.

- If an existing **open** PR addresses the same failure, read its current head, checks, and conversation. For the configured bot's PR, continue the repair per `/tend-ci-runner:continue-pr`, then stop this workflow without opening another PR. For a human's in-flight fix, leave the work with them; post only new diagnostic evidence per `/tend-ci-runner:post-to-github`.
- If a **closed** PR with a maintainer rejection covers the same failure, exit silently; check the closure comment for the rationale before referencing it. Re-deriving the same fix forces a maintainer to close it twice.

Also check for open tracking issues left by a prior unfixable diagnosis (see 4b) — compare candidate diagnoses per **Claims of recurrence** in `/tend-ci-runner:ground-claims`. If one covers the current failure, the fix PR you eventually open should reference it via `Fixes #<n>` so the issue closes when the PR merges:

```bash
BOT_LOGIN=$(gh api user --jq '.login')
gh issue list --state open --author "$BOT_LOGIN" --search "ci-fix: in:title" \
  --json number,title,body --limit 100
```

### 3. Diagnose and fix

The gates every fix clears are in `/tend-ci-runner:fix-a-bug`; read it before writing one.

1. Identify the root cause — don't just fix the symptom
2. Search for the same pattern elsewhere in the codebase
3. Reproduce locally using test commands from the project's instruction files
4. Fix at the right level (shared helper > per-file fix)

### 4. Create PR

Re-run step 2's author-agnostic PR query, then follow **Dedup recheck immediately before `gh pr create`** in `/tend-ci-runner:open-pr` to check for a fix committed to the default branch. If the failure no longer reproduces there, don't open the PR. Before the push, review the change per **Review the change before the push** in `/tend-ci-runner:push-commits`.

```bash
git checkout -b fix/ci-<run-id>
git add <files>
git commit -m "fix: <description>"
git push -u origin fix/ci-<run-id>
```

Create the PR with `gh pr create`, composing its body in a file per `/tend-ci-runner:post-to-github`. Write for a maintainer deciding whether the current fix addresses the failure: explain the causal finding, why the change fixes it at the right level, and the verification relevant to that decision. Link the failed run as supporting evidence and follow **Reader-facing prose** in `/tend-ci-runner:run-tend`.

### 4a. Diagnosis without a fix (transient causes)

If the diagnosis identifies the failure as transient — runner-disk corruption, an isolated network blip, an upstream incident that has since resolved — there is no fix PR to create. Don't post the diagnosis as a commit comment (it surfaces on whatever commit triggered CI, including release commits where it's visibly off-topic).

Use this path only when the evidence points to ephemeral infrastructure, not anything the project's code does. Signals (examples, not a checklist): the same code path passed on recent prior runs with no relevant change; the failure shape is filesystem/network-level; an upstream status incident matches the timing and components. If you can't tell whether it's transient, treat it as durable — create a fix PR, or follow 4b if a safe fix can't be produced.

#### Reassess transient diagnoses

Before filing the tracker below, inspect related prior diagnoses. A passing rerun does not establish that an intermittent cause has gone away.

```bash
BOT_LOGIN=$(gh api user --jq '.login')
gh issue list --state all --label tend-outage --author "$BOT_LOGIN" \
  --search "ci-fix: transient failure in:title" \
  --json number,title,body,createdAt --limit 100
```

The label and title distinguish these diagnoses: the label keeps 4b's durable trackers out, and the title keeps out the `report_failure.py` **"Bot temporarily unavailable"** issues, which carry the same label and author and vastly outnumber these — without it the first page can hide the relevant diagnoses.

Use failure-shape keywords (e.g. `rustup-init`, `composer connect timeout`,
`docker pull rate limit`) to find candidates. Read their diagnoses and compare
current evidence per **Claims of recurrence** in
`/tend-ci-runner:ground-claims`. Related reports are leads, not proof that the
same cause persists.

Classify by the present cause and its recovery evidence. A reproducible
project or upstream defect remains durable even when it first appears today
or some reruns pass. A resolved external incident can explain many failures
without leaving a defect to repair. Where a durable cause remains, propose a
mitigation that addresses it safely, preferring an upstream-documented
workaround and linking its evidence. If no safe fix can be produced, follow
4b. Otherwise, when the evidence supports recovery, file the transient tracker.

#### File the transient tracker

If the failure stays classified transient, open an issue with the diagnosis per `/tend-ci-runner:open-pr` and close it immediately. The closure records "diagnosed, no further action" while keeping the analysis discoverable and off the commit timeline. Apply the `tend-outage` label so triage skips the tracker. Mention verification rejects the bot’s own comments before starting an agent; human replies can still request work:

```bash
gh label create tend-outage --description "Tracks bot outage incidents" --color "d93f0b" 2>/dev/null || true
gh issue create --title "ci-fix: transient failure on <run-id>" --label tend-outage --body-file "$TMPDIR/diagnosis.md"
gh issue close <issue-number> --reason "not planned" --comment "Transient — closing as diagnosed."
```

Skip step 5 — there's no PR to monitor.

### 4b. Diagnosis without a fix (durable causes)

If the diagnosis identifies a durable root cause but a safe fix can't be produced — the cause is in an external system the bot can't change, the fix requires judgment the bot shouldn't make unilaterally, or an attempted fix didn't validate locally — leave a tracking issue. Without one, a durable failure that the bot can't fix lives only on the workflow-run page and is invisible in the issues list.

Leave the issue **open**, so maintainers have a durable "still broken" signal until a fix ships. A subsequent fix PR closes it via `Fixes #<n>` in the PR body (see step 2 — search for a matching open tracking issue before opening the fix PR).

**Dedup first.** Search for an open tracking issue covering the same failure shape; if one exists, post an update on it rather than opening a duplicate. Use failure-shape keywords to find candidates, then match their diagnostic evidence per **Claims of recurrence** in `/tend-ci-runner:ground-claims`, not run ID — each run ID is unique and won't dedup:

```bash
BOT_LOGIN=$(gh api user --jq '.login')
gh issue list --state open --author "$BOT_LOGIN" --search "ci-fix: in:title" \
  --json number,title,body --limit 100
```

Read each candidate tracker's whole thread before matching or composing a recurrence update — the listing above returns only the body:

```bash
gh issue view <issue-number> --json body,comments
```

When the diagnostic comparison establishes a match, update that tracker. The comment is an update for people following the tracker, not a log entry for this run. They already know CI is failing and have read the earlier reports, so tell them what they don't know yet: what changed since the thread's last update — failures that are new or now pass, a fix that merged or is still in flight, a revised diagnosis — or, when nothing did, that the same failures continue and this run is the latest case. Write it from that delta rather than from the shape of the earlier comments; the run link and supporting diagnosis back it up rather than open it. Compose `$TMPDIR/recurrence.md` and post per `/tend-ci-runner:post-to-github`:

```bash
gh issue comment <issue-number> --body-file "$TMPDIR/recurrence.md"
```

Otherwise, open a new tracking issue per `/tend-ci-runner:open-pr`. Use a title prefix that future runs can search on (`ci-fix: <workflow-name> failing`) with a short root-cause suffix for human readability:

```bash
gh issue create \
  --title "ci-fix: <workflow-name> failing — <short root cause>" \
  --body-file "$TMPDIR/diagnosis.md"
```

Compose `$TMPDIR/diagnosis.md` as a durable account for a maintainer deciding what happens next. Include the failed workflow and run, the root cause and mechanism, why no safe automated fix was produced, and the current blocker or decision. Follow **Reader-facing prose** in `/tend-ci-runner:run-tend`; the issue should preserve the conclusion, not the diagnostic transcript.

Skip step 5 — there's no PR to monitor.

### 5. Monitor CI

Wait for CI per `/tend-ci-runner:monitor-ci`.
