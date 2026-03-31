---
name: review-runs
description: Daily review of the previous night's CI runs — identifies problems and improves repo-local skills and workflows.
metadata:
  internal: true
---

# Review Runs

Analyze the previous night's Claude CI runs in this repository. Identify behavioral problems, skill
gaps, and workflow issues — then propose improvements to the repo's local skills and workflows.

This skill runs **in the adopter repo**, not in tend. Improvements target `.claude/skills/` and
`.config/tend.toml` in this repository.

## First steps

```bash
ls .claude/skills/
```

Load any repo-specific skill overlay before proceeding.

## Confidence and magnitude gates

Before creating a PR, every finding must pass two gates.

### Gate 1: Confidence — is this a real problem?

| Evidence level | Meaning | Minimum occurrences to act |
|---|---|---|
| **Critical** | Clearly wrong outcome (closed wrong issue, merged broken code, deleted user data) | 1 |
| **High** | Consistent pattern across multiple sessions | 2-3 |
| **Medium** | Plausible problem seen once, could be noise | 5+ |
| **Low** | Nitpick or stylistic preference | Do not act |

Occurrences include both the current analysis **and** historical evidence from the tracking issue
(see [Evidence accumulation](#evidence-accumulation)).

If a finding doesn't meet the threshold, **skip it** — don't create a PR, don't create an issue,
don't comment. Record it in the tracking issue so it can accumulate evidence over future runs.

### Gate 2: Magnitude — is the fix proportionate?

| Change type | Examples | Evidence bar |
|---|---|---|
| **Removal / simplification** | Remove confusing sentence, delete dead guidance | Low (1 occurrence is enough) |
| **Targeted fix** | Fix a specific incorrect instruction, add a missing step | Normal (use Gate 1 thresholds) |
| **New paragraph or section** | Add explanation of a concept, new workflow guidance | High (need 3+ occurrences showing the gap) |
| **Structural change** | Reorganize a skill, add a new skill file, change workflow | Very high (need 5+ occurrences or a critical failure) |

### Structural vs. stochastic failures

- **Structural**: deterministic cause that guidance can prevent — will recur every time the same
  conditions arise. One clear occurrence is sufficient for a targeted fix.
- **Stochastic**: probabilistic model behavior — the same model might handle the next identical
  situation correctly. These need 5+ occurrences.

The test: "If I replayed this exact scenario 10 times, would the failure occur every time
(structural) or only sometimes (stochastic)?" When in doubt, classify as stochastic.

### Applying the gates

For each finding, state:
1. The evidence level and occurrence count (current + historical)
2. Whether the failure is structural or stochastic
3. The proposed change type
4. Whether it passes both gates

Only proceed to Step 5 for findings that pass both gates.

## Evidence accumulation

Each run only sees a window of CI sessions, but patterns may emerge over days or weeks. Use a
**monthly tracking issue** to accumulate evidence across runs.

### Finding or creating the tracking issue

```bash
MONTH=$(date +%Y-%m)
gh issue list --state open --label review-runs-tracking \
  --json number,title --jq ".[] | select(.title | contains(\"$MONTH\"))"
```

If none exists for the current month, create one:

```bash
cat > /tmp/tracking-body.md << 'EOF'
Monthly tracking issue for review-runs findings that haven't yet met the confidence threshold. Each run appends below-threshold findings as a comment. Future runs read these to build cumulative evidence.

**Do not close manually** — a new issue is created each month.
EOF
gh issue create \
  --title "review-runs tracking: $MONTH" \
  --label review-runs-tracking \
  -F /tmp/tracking-body.md
```

### Reading historical evidence

Before applying the gates, read the current tracking issue's comments to find prior observations:

```bash
TRACKING_NUMBER=<number from above>
gh issue view "$TRACKING_NUMBER" --json comments \
  --jq '.comments[] | {author: .author.login, body: .body}'
```

Also check last month's tracking issue (if it exists) for recent carry-over.

When a historical entry looks like it might match a current finding, **download and investigate the
linked workflow's session logs** to confirm the pattern genuinely matches.

### Recording below-threshold findings

After analysis, find **the bot's existing comment** on the tracking issue and **append** new
findings. If no bot comment exists yet, create one.

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
BOT_LOGIN=$(gh api user --jq '.login')
EXISTING_COMMENT=$(gh api "repos/$REPO/issues/$TRACKING_NUMBER/comments" \
  --jq "[.[] | select(.user.login == \"$BOT_LOGIN\")] | last | .id // empty")
```

If `EXISTING_COMMENT` is non-empty, download existing body, append new findings, then PATCH.

Format each finding under a `## Run <run-id>` heading with evidence level, occurrences, workflow
link, and detail.

## Step 1: Find recent runs

List Claude CI runs that completed overnight (past 12 hours):

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
SINCE=$(date -u -d '12 hours ago' +%Y-%m-%dT%H:%M:%SZ)
for workflow in $(gh api repos/$REPO/actions/workflows --jq '.workflows[] | select(.name | startswith("tend-")) | .id'); do
  gh api "repos/$REPO/actions/workflows/$workflow/runs?created=>=$SINCE&status=completed" \
    --jq '.workflow_runs[] | {databaseId: .id, conclusion, createdAt: .created_at, name: .name}'
done
```

If no runs found, report "no runs to review" and exit.

## Step 2: Download and analyze session logs

Load `/install-tend:debug-ci-session` for download commands and JSONL parsing queries.

Skip runs without artifacts. Trace decision chains: what did Claude decide, what evidence did it
use, what was the outcome?

## Step 3: Cross-check outcomes

For each analyzed run, compare what the bot did against what happened next:

- **Review runs**: Did subsequent commits undo something the bot approved? Did human reviewers flag
  issues the bot missed?
- **Triage runs**: Was the bot's classification correct? Did the issue get relabeled?
- **Nightly runs**: Did the bot's PRs get merged, or were they closed as unhelpful?
- **CI-fix runs**: Did the fix actually resolve the CI failure?

```bash
# Example: check if a bot PR was merged or closed
gh pr list --author "$BOT_LOGIN" --state all --json number,title,state,closedAt \
  --jq '.[] | select(.closedAt > "'$SINCE'")'
```

## Step 4: Deduplicate

Before creating issues or PRs, check for existing ones:

```bash
gh issue list --state open --json number,title,body
gh pr list --state open --json number,title,headRefName,body
gh issue list --state closed --json number,title,closedAt --limit 30
```

Search titles AND bodies for related keywords.

## Step 5: Act on findings

Improvements target **repo-local** files:

- **`.claude/skills/`** — update or create skill overlays with guidance that prevents the
  identified problem. Prefer updating existing skill files over creating new ones.
- **`.config/tend.toml`** — adjust workflow configuration if the problem is structural (e.g.,
  wrong cron schedule, missing setup step).
- **`CLAUDE.md`** — add project-specific guidance if the problem is about code conventions or
  patterns the bot keeps getting wrong.

**Prefer PRs over issues.** A PR with a clear description is immediately actionable.

- **PR** (default): Branch `daily/review-runs-$GITHUB_RUN_ID`, fix, commit, push, create with
  label `review-runs`. Put full analysis in PR description (run IDs, log excerpts, root cause,
  gate assessment).
- **Issue** (fallback): Only for problems too large or ambiguous to fix directly.

**Limit to at most 2 PRs per run.** Pick the highest-confidence findings; note the rest in the
tracking issue.

## Step 6: Summary

If no problems found (or none passed the gates), report "all clear" with: runs analyzed, sessions
reviewed, brief quality assessment, and any below-threshold findings recorded in the tracking
issue.
