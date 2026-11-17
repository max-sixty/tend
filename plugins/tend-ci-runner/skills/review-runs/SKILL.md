---
name: review-runs
description: Daily review of the previous night's CI runs — identifies problems and improves repo-local skills and workflows.
metadata:
  internal: true
---

# Review Runs

Analyze the previous night's tend CI runs in this repository. Identify behavioral problems, skill gaps, and workflow issues — then propose improvements to the repo's local skills and workflows.

This skill runs **in the consumer repo**, not in tend. Improvements target `.claude/skills/` and `.config/tend.yaml` in this repository.

## Load required skills

- Load `/tend-ci-runner:run-tend` first, including its repo-specific overlay.
- Read `references/parallel-maintenance.md` to divide the sweep's work.
- Load `/tend-ci-runner:post-to-github` and `/tend-ci-runner:open-pr` before composing posts or opening PRs.
- Load `/tend-ci-runner:read-session-logs` before Step 3.
- Load `/tend-ci-runner:act-in-other-repos` before filing a bundled-skill defect upstream, or `/tend-ci-runner:propose-instructions` before editing repo-local skills.
- Load `/tend-ci-runner:push-commits` before pushing a fix and `/tend-ci-runner:monitor-ci` after the push.

```bash
ls .claude/skills/
```

@review-gates.md

## Evidence accumulation

Each run contributes to a monthly `review-runs-tracking` issue. Prepare the
current tracker and read the current and previous month's evidence:

```bash
tend runtime review-runs prepare-evidence \
  > "$TMPDIR/evidence.json"
jq -r '.current_comments[].body' "$TMPDIR/evidence.json" > "$TMPDIR/evidence-current.md"
jq -r '.previous_comments[].body' "$TMPDIR/evidence.json" > "$TMPDIR/evidence-previous.md"
jq -r '"tracker #\(.tracking_number) \(.month)"' "$TMPDIR/evidence.json"
```

The command creates this month's tracker when needed, closes older open
trackers, persists the current issue id, and returns both evidence windows.
Redirect it rather than reading it inline: an established tracker's two
windows run to hundreds of kilobytes, past what a tool result carries. The
files also preserve prior observations and their diagnoses. Each entry carries
its own session's wording, so grepping for this run's phrasing undercounts:
list the finding headings first
(`grep -h '^### ' "$TMPDIR"/evidence-*.md`), match on meaning, then read the
`## Run` entry behind each candidate.

After analysis, write the new findings in the format from `@review-gates.md`
to `$TMPDIR/findings.md`. Include a literal `## Run $GITHUB_RUN_ID` heading.
Then append them:

```bash
tend runtime review-runs append-evidence
```

The command refuses a findings file that does not name this run. It appends to
the latest bot evidence comment while the combined body remains below 60 KB,
then starts a new comment. Prior entries are never replaced.

## Step 1: Find recent runs

List tend CI runs that completed since the previous `review-runs` run (nominally 24 hours — the cron runs daily):

```bash
tend runtime list-recent-runs review-runs
```

If no runs are found, report "no runs to review", check **Wedged queues** and drain the trackers under **Outage trackers** below, then exit.

Report the run census as the number of rows this returns. Carry each stderr `WARNING` into the summary as a coverage gap rather than an all-clear — except the re-run warning, which instead names earlier attempts whose logs to read before counting the window's failures.

Then, for each run ID from above, pull its jobs and classify them:

- **Long-running** (>30 min): Tend runs typically finish in single-digit minutes. Anything over 30 is worth a look — download session logs in Step 3 and diagnose where the time went (long background waits, push-wait-fix cycles, a stuck tool call).
- **Near-timeout** (within 90% of the cap): A job that consumed most of its timeout budget is one slow external check away from being killed. Judge it by what the kill would leave on the record. Usually nothing — a cron-driven run a later tick repeats. An outward action can leave unfinished obligations: a `tend-review` job killed mid-poll leaves its approval standing over red CI.

To determine the timeout cap for a workflow, read `timeout-minutes` from that workflow's own file under `.github/workflows/` — the census admits workflows named outside the `tend-` prefix, so don't glob for one. Tend's generated workflows do not set `timeout-minutes`, so GitHub's 360-minute default applies unless the consumer has overridden it via `workflows.<name>.jobs.<job>.timeout-minutes` in `.config/tend.yaml`.

```bash
# Flag long-running and near-timeout jobs
gh api "repos/$GITHUB_REPOSITORY/actions/runs/$RUN_ID/jobs" \
  --jq '.jobs[]
    | ((.completed_at | fromdateiso8601) - (.started_at | fromdateiso8601)) as $dur
    | select($dur >= 1800)   # 30 min
    | {name, conclusion, duration_min: ($dur / 60 | floor), url: .html_url}'
```

After retrieving the timeout cap from the workflow file, flag any job whose duration exceeded 90% of it as a near-timeout. For the default 360-min cap, that threshold is 324 min.

Keep each failed run in the summary as diagnostic evidence, whether or not its work has since been recovered.

### Wedged queues

A run parked in GitHub's pre-job `waiting` state holds its concurrency group without ever concluding, so under `cancel-in-progress: false` every later tick takes the single pending slot and is replaced by its successor — the workflow stops running and nothing fails. The census above cannot see the parked run, since it admits a row only on a non-null `conclusion`, and the replacements it causes read there as ordinary concurrency.

```bash
gh api --paginate "repos/$GITHUB_REPOSITORY/actions/runs?status=waiting&per_page=100" \
  --jq '.workflow_runs[] | {id, path, created_at, html_url}'
```

A tend run still `waiting` after several of its own scheduling intervals — or, on an event-driven workflow, long after the event that created it — is wedged. Every generated workflow but the secretless `tend-mention-relay` carries the `tend` environment, so any of the rest can park. `pending_deployments` on it confirms which kind: `wait_timer: 0` with an empty `reviewers` is an environment gate with nothing left to release it, so `gh run cancel <id>` is the remedy and the pending successor starts. `queued` is a different state and not this shape — `gh run cancel` there answers `Cannot cancel a workflow run that is completed` while the runs API still reports the run `queued`. That is GitHub bookkeeping holding nothing live; leave it rather than fighting it.

### Outage trackers

Read every row on every outage tracker this sweep still owes a drain. Each
tracker's `rows` holds them in order: the issue body carries the first,
comments the rest. Fail the sweep if the script exits non-zero; that is
different from it returning no trackers:

```bash
tend runtime review-runs outage-trackers \
  > "$TMPDIR/review-runs-outage.json"
```

It returns the open trackers plus any closed since Step 1's anchor by someone
other than the bot, since only this drain closes a drained tracker. Read the
closed ones too: current repository state can't tell a merged PR whose review
died in the outage from one the maintainer merged without waiting.

Use every row to identify what the failed run may have missed. Diagnose it and
handle any applicable current work. Then close the trackers still open; one
someone else already closed stays closed:

```bash
jq -r '.trackers[] | select(.state == "OPEN") | .number' \
  "$TMPDIR/review-runs-outage.json" \
  | while read -r number; do gh issue close "$number" --reason completed; done
```

## Step 2: Token usage report

Run the token report script to get per-run token counts:

```bash
# Step 1's own anchor, so the spend prices exactly the band the census counts:
# the script fetches with a cushion and admits a run on completion, the way
# Step 1 does. A window in hours would drop a run that started before the
# anchor and finished inside it — the longest and costliest runs there are.
tend runtime token-report \
  --since "$(cat "$TMPDIR/review-runs-since")" \
  > "$TMPDIR/token-report.json"
```

Pass the same extra prefixes Step 1 censuses, as positional arguments after `--since`, so the two steps agree on what the fleet is — the repo's `running-tend` skill is the source for both, naming any workflow that uses the tend action but isn't named `tend-*`.

Include the total cost and the per-workflow breakdown in the summary (Step 7). Escalate outliers to Step 3 — for example a run far above its workflow's usual cost, or a subject the subject table shows several runs against.

## Step 3: Download and analyze session logs

Use `/tend-ci-runner:read-session-logs` to pick the runs and `/install-tend:debug-tend-run` for the download commands and JSONL parsing queries.

Skip runs without artifacts. Trace decision chains: what did tend decide, what evidence did it use, what was the outcome?

## Step 4: Cross-check outcomes

For each analyzed run, compare what the bot did against what happened next. The same "did it stick?" question applies to every tend workflow — ask it of whatever ran. For example:

- **Review**: did subsequent commits undo something the bot approved? Did human reviewers flag issues it missed?
- **Triage**: was the classification correct? Did the issue get relabeled?
- **Nightly**: did the bot's PRs merge, or get closed as unhelpful?
- **CI failure reports**: did each cause's fix resolve its failure, and did each report close once its failures were diagnosed and owned, and reopen on the lane's next failure?

mention, notifications, and weekly runs get the same treatment: find the bot's output and check whether it was accepted.

Dispositions — merged, closed, relabeled, reverted — are only half the signal. A maintainer replying in-thread that a bot claim was wrong, or requesting changes on a bot PR, leaves labels and state untouched and is equally a correction; where the bot authors most of the PRs, a review body is the *first* place a maintainer writes. The script collects all three — dispositions, thread comments, review bodies — for the window:

```bash
tend runtime review-runs-corrections \
  "$(cat "$TMPDIR/review-runs-since")"
```

Read every row: a correction is a maintainer contradicting a bot claim, not merely replying. Every comment and review row names its `author` — prose style doesn't decide, since a maintainer can post through an agent; an empty `author` is a deleted account, never the bot. Comment rows carry both timestamps because the window filters on `updated_at` — a `created` before the anchor is an older comment edited inside the window, a real hit rather than a broken filter. Empty `dispositions`, `comments`, and `reviews` is the all-clear.

Write "no maintainer corrections" into the tracking issue only after the script ran and returned empty — future runs treat it as evidence, so an unchecked all-clear hides observations they need. The script exits non-zero rather than reporting an empty window when the anchor or the bot login is missing, since both filters fail open.

## Step 5: Deduplicate

Before creating issues or PRs, search for existing ones. A recency listing drops an older match past any `--limit`, so search instead; one search per distinctive term — the skill, component, or symptom the finding names:

```bash
# Search matches titles and bodies. --state all: a merged PR is the most
# common way a finding is already fixed.
gh issue list --state all --search "<keywords>" --limit 100 --json number,title,state
gh pr list --state all --search "<keywords>" --limit 100 --json number,title,state
# Bundled-skill defects are filed upstream (Step 6), and the queries above only
# see this repo — dedup against tend before filing there.
gh issue list --repo max-sixty/tend --state all --search "<keywords>" --limit 100 --json number,title,state
gh pr list --repo max-sixty/tend --state all --search "<keywords>" --limit 100 --json number,title,state
```

Then read the bodies of the candidates (`gh pr view <n> --json body`).

Your workflows call a pinned action ref, so a skill fix merged upstream stays dormant here until the next release tags. Observing the bug is therefore not evidence the fix is missing: read these results before filing, or the report is churn on something already landed.

## Step 6: Act on findings

Delegate independent product repairs per `references/parallel-maintenance.md`.
The behavioral and workflow improvements below target **repo-local** files by default:

- **`.claude/skills/`** — update or create skill overlays with instructions that prevent the identified problem. Prefer updating existing skill files over creating new ones.
- **`.config/tend.yaml`** — adjust workflow configuration if the problem is structural (e.g., wrong cron schedule, missing setup step).
- **Project instruction file (`CLAUDE.md` or `AGENTS.md`)** — add project-specific instructions if the problem is about code conventions or patterns the bot keeps getting wrong.

**Bundled-skill defects.** If the root cause is a gap or bug in a bundled skill (`plugins/tend-ci-runner/skills/...` in `max-sixty/tend`) — the same pattern would fire in every consumer — file the fix against tend per `/tend-ci-runner:act-in-other-repos`. Signal: the fix reads as generic instructions that would apply to any consumer.

Editing `.claude/skills/` requires the read-only-mount workaround (bind-mounted read-only, plus a harness write-guard on `.claude/skills/` paths) — see `/tend-ci-runner:propose-instructions`. Adapted for review-runs (base on `HEAD` since this runs on a schedule, not a PR checkout; move each edited file into place). Choose a topic slug for each theme so separate PRs from the same run have distinct branches:


```bash
TOPIC=workflow-evidence # replace with the repair's theme
git worktree add "$TMPDIR/review-runs-fix" -b "daily/review-runs-$GITHUB_RUN_ID-$TOPIC" HEAD

# Author each edited skill file at $TMPDIR/<name>.md.
# Then move the files into place. Both `cd`s stay inside subshells, so the
# session's own cwd never enters the worktree: the last line deletes it, and a
# session standing in it has no working directory for Step 7 or anything after.
( cd "$TMPDIR/review-runs-fix/.claude/skills/running-tend" && mv "$TMPDIR/running-tend.md" SKILL.md )
# Repeat per skill file being updated.

(
  set -e
  cd "$TMPDIR/review-runs-fix"
  git add .claude/skills/
  git commit -m "skills(running-tend): ..."
  git push -u origin HEAD
  gh pr create --title "..." --body-file "$TMPDIR/pr-body.md" --head "$(git branch --show-current)"
) && git worktree remove "$TMPDIR/review-runs-fix" --force
```

`.config/tend.yaml` and project instruction files are not under the read-only mount, but if you're already in the worktree for a `.claude/skills/` edit, do those edits there too so the branch stays self-contained.

- **PR** (default): Branch `daily/review-runs-$GITHUB_RUN_ID-<topic-slug>`, fix, commit, push, create with label `review-runs`. Write the description for a maintainer deciding whether the current change fixes the general behavior gap, following **Reader-facing prose** in `/tend-ci-runner:run-tend`. Link the tracking issue where it holds prior observations of the same behavior, and carry the evidence that justified promoting this finding — the run IDs, the log excerpt, and the causal assessment — in the body or a `<details>` block.
- **Issue**: Delegate or preserve unfinished work per **Filing issues in this repo** in `/tend-ci-runner:open-pr`.

Group related findings by theme and complete the supported repairs. Record unresolved findings in the tracking issue.

## Step 7: Summary

Summarize runs analyzed, sessions reviewed, supported repairs, and unresolved findings recorded in the tracking issue. Report "all clear" only when no problems remain.

Save the summary to `$GITHUB_STEP_SUMMARY` (a later workflow step copies this into the GitHub Actions step summary):

```bash
cat > "$GITHUB_STEP_SUMMARY" << 'EOF'
## Review-runs summary
...
EOF
```
