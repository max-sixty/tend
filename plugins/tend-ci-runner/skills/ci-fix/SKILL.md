---
name: ci-fix
description: Debug and fix failing CI on the default branch. Use when CI fails on main.
argument-hint: "[run-id and context]"
metadata:
  internal: true
---

# Fix CI on Default Branch

CI has failed on the default branch. Diagnose the root cause, fix it, and create a PR.

**Failed run:** $ARGUMENTS

## Workflow

### 0. Load environment skills

Load `/tend-ci-runner:running-in-ci` first — it contains CI security rules, polling conventions, and comment formatting guidance. It will also prompt you to load any repo-specific skills (e.g., `running-tend`).

### 1. Check for existing fixes

Check both open and recently closed bot-authored `fix/ci-*` PRs. A maintainer may have closed a prior workaround with a rejection rationale (e.g. "we'll fix upstream"); re-deriving the same fix forces them to close it twice.

```bash
BOT_LOGIN=$(gh api user --jq '.login')

# Open dedup:
gh pr list --state open --head "fix/ci-" --json number,title,body,headRefName

# Closed dedup — last ~14 days, bot-authored:
gh pr list --state closed --author "$BOT_LOGIN" --search "head:fix/ci-" \
  --json number,title,closedAt,body,headRefName \
  --jq '[.[] | select((now - (.closedAt | fromdateiso8601)) < 1209600)] | .[]'
```

Match by **failure shape** (the diagnostic snippet in the PR body) rather than branch name — branch names encode run IDs and never repeat. If a closed PR with a maintainer rejection covers the same failure, exit silently; check the closure comment / review for the rationale before referencing it.

If an existing open PR addresses the same failure, comment on it linking the new run and stop.

Two gotchas:

- Use the `head:fix/ci-` **search qualifier** (or `--head fix/ci-` — gh translates it to the same query). Don't use `in:head` — that's silently dropped and falls back to default-field text matching.
- Request `body` in `--json` for both queries — the failure diagnostic written by the prior ci-fix run lives there, and shape-matching needs it.

### 2. Diagnose and fix

1. Get failure logs: `gh run view <run-id> --log-failed`
2. Identify the failing job and root cause — don't just fix the symptom
3. Search for the same pattern elsewhere in the codebase
4. Reproduce locally using test commands from the project's CLAUDE.md
5. Fix at the right level (shared helper > per-file fix)

### 3. Create PR

Re-check for existing fix PRs (one may have been created while you worked).

```bash
git checkout -b fix/ci-<run-id>
git add <files>
git commit -m "fix: <description>

Co-Authored-By: Claude <noreply@anthropic.com>"
git push -u origin fix/ci-<run-id>
```

Create the PR with `gh pr create`. PR body format:

```
## Problem
[What failed and the root cause]

## Solution
[What was fixed and why this is the right level]

## Testing
[How the fix was verified]

---
Automated fix for [failed run](run-url)
```

### 3a. Diagnosis without a fix (transient causes)

If the diagnosis identifies the failure as transient — runner-disk corruption, an isolated network blip, an upstream incident that has since resolved — there is no fix PR to create. Don't post the diagnosis as a commit comment (it surfaces on whatever commit triggered CI, including release commits where it's visibly off-topic).

Instead, open an issue with the diagnosis and close it immediately. The closure records "diagnosed, no further action" while keeping the analysis discoverable and off the commit timeline:

```bash
gh issue create --title "ci-fix: transient failure on <run-id>" --body-file /tmp/diagnosis.md
gh issue close <issue-number> --reason "not planned" --comment "Transient — closing as diagnosed."
```

Use this path when:
- The same code path succeeded on a recent prior run with no relevant changes between runs
- The failure shape is filesystem/network-level, not anything the project's code does
- An upstream status incident matches the timing and components

If you can't tell whether it's transient, treat it as durable and create a fix PR.

Skip step 4 — there's no PR to monitor.

### 4. Monitor CI

Poll CI using the approach from `running-in-ci` (loaded in step 0). If CI fails, diagnose with `gh run view <run-id> --log-failed`, fix, commit, push, and repeat.
