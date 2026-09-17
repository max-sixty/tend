---
name: triage
description: Triages new GitHub issues — classifies, reproduces bugs, attempts conservative fixes, and comments. Use when a new issue is opened and needs automated triage.
argument-hint: "[issue number]"
metadata:
  internal: true
---

# Issue Triage

Triage a newly opened GitHub issue.

**Issue to triage:** $ARGUMENTS

## Step 1: Setup

Load `/tend-ci-runner:running-in-ci` first (CI environment rules, security). It will also prompt you to load any repo-specific skills (e.g., `running-tend`) — do so before proceeding.

Reproduce before fixing, find evidence before speculating, and test before committing.

## Step 2: Read and classify the issue

```bash
gh issue view $ARGUMENTS --json title,body,labels,author
```

An issue the bot itself opened — a nightly failure, a CI report, a code-quality finding — is a report to act on, not a self-conversation: the system prompt's self-loop guard covers the bot's own *comments*, and triage runs on these normally while no bot comment answers them yet.

Classify into one of:

- **Bug report** — describes unexpected behavior, includes steps to reproduce or error output. Descriptions of changed behavior ("no longer works", "used to work") strongly signal a bug even with a terse body.
- **Feature request** — asks for new functionality or behavior changes
- **Question** — asks how to do something or how something works
- **Other** — doesn't fit the above categories

## Step 3: Check for duplicates

*Skip for questions and other.*

```bash
# Search open issues for similar problems
gh issue list --state open --json number,title,labels --limit 200

# Check for existing fix branches and PRs
git branch -r --list 'origin/fix/*'
gh pr list --state open --json number,title,headRefName --limit 200
```

If a duplicate or existing fix is found, note it for the comment in step 7. Don't create a duplicate fix.

## Step 4: Investigate existing functionality

*Feature requests only.*

Search the codebase to check whether the requested feature already exists.

1. **Extract the core ask** — What specific behavior does the requester want?
2. **Search for implementations** — Grep for relevant function names, config keys, CLI flags, and domain terms.
3. **Read key files** — If searches find hits, read the relevant source to understand what already exists and how it works.
4. **Check docs and help text** — Look for user-facing documentation of the feature.

Record what you found (or didn't find) for use in step 7.

## Step 5: Reproduce the bug

*Bug reports only.*

Follow **Reproduce first** in `references/fixing.md`: a failing test in an existing test file, run to confirm it fails.

If you cannot reproduce the bug (unclear steps, environment-specific, etc.), note what you tried and skip to step 7. If the test passes, the bug may already be fixed — note that for the comment.

## Step 6: Fix (conservative)

*Bug reports only.*

`references/fixing.md` carries the gates: the reproduction gate, the conditions a fix attempt needs, skill-text fixes, the shapes of bad fix, and the local bar before pushing. Read it before writing any fix. Where a gate fails, go to Step 7 and report the outcome you established.

### If fixing

1. Clear the local bar in `references/fixing.md`.
2. Create branch, commit, push, and create PR:
   ```bash
   git checkout -b fix/issue-$ARGUMENTS
   git add -A
   git commit -m "fix: <description>

   Closes #$ARGUMENTS"
   git push -u origin fix/issue-$ARGUMENTS
   ```
   Compose the body at `$TMPDIR/pr-body.md`. Write for a maintainer deciding whether the current fix resolves the issue: explain the causal finding, the resulting behavior change, and the reproduction test that now passes. Follow **Reader-facing prose** in `/tend-ci-runner:running-in-ci`, and end with `Closes #$ARGUMENTS — automated triage` so merging closes the issue.

   <example>
   <bad reason="Restates the report, narrates the investigation, and claims a generic test run">

   Bad:

   ```markdown
   The issue reports that retries fail. I inspected the retry loop, compared several paths, and changed three files. I ran the test suite.
   ```

   </bad>
   <good reason="Carries the cause, the resulting behavior, and the evidence a reviewer needs">

   Good:

   ```markdown
   A retry dropped the resolved workspace root, so its second attempt read from the process directory and failed outside the repository. Retry state now keeps the root, so both attempts address the same workspace. The regression test reproduces the second-attempt failure before the change and passes after it.

   Closes #123 — automated triage
   ```

   </good>
   </example>

   ```bash
   gh pr create --title "fix: <description>" --body-file "$TMPDIR/pr-body.md"
   ```
3. Wait for CI per `references/ci-monitoring.md` in `/tend-ci-runner:running-in-ci`.

### If reproduction test works but fix is not confident

Commit just the failing test on a reproduction branch and open a PR:

```bash
git checkout -b repro/issue-$ARGUMENTS
git add -A
git commit -m "test: add reproduction for #$ARGUMENTS"
git push -u origin repro/issue-$ARGUMENTS
```

Compose the body at `$TMPDIR/pr-body.md`. Make clear that the PR deliberately adds a failing reproduction without a fix, what behavior it captures, and any causal boundary already established so a maintainer knows what remains to decide. Follow **Reader-facing prose** in `/tend-ci-runner:running-in-ci`, and end with `Automated triage for #$ARGUMENTS`.

```bash
gh pr create --title "test: reproduction for #$ARGUMENTS" --body-file "$TMPDIR/pr-body.md"
```

Note the PR number for the comment.

## Step 7: Comment on the issue

Re-fetch before posting, per **Recheck before posting** in `/tend-ci-runner:running-in-ci`'s `references/posting.md` — triage can take minutes, so re-fetch the issue and skip any point a new human comment or a sibling tend workflow already covered.

Always comment via `gh issue comment`. Write for the issue author: lead with the current disposition, then give the causal finding and the action taken or the one concrete input or decision still needed. Link any fix, reproduction, or duplicate. Follow **Reader-facing prose** in `/tend-ci-runner:running-in-ci`; do not restate the report or narrate the investigation. Never claim the issue is fully resolved by automation alone — an opened fix still needs maintainer review and landing. Acknowledge the reporter when the situation calls for it, but do not use thanks or maintainer deferrals as fixed openers and closers. Do not present the bot's judgment as a maintainer decision.

Read the reporter's relationship to the repository before composing the reply:

```bash
gh api "repos/$GITHUB_REPOSITORY/issues/$ARGUMENTS" --jq '.author_association'
```

Omit a maintainer-deferral closer when `author_association` is `OWNER`, `MEMBER`, or `COLLABORATOR`; deferring to a maintainer reads as absurd when the reporter is one. For other reporters, a natural boundary can signal that the bot's action is not authoritative. This is a role distinction, not prescribed wording.

**Stay within what you verified.** State facts you found in the codebase — don't characterize something as "known" unless you find prior issues or documentation about it. Don't speculate beyond the code you read.

**Report the finding, not the search.** For a feature that plainly doesn't exist yet, "I searched the codebase and didn't find an existing implementation" only restates what the requester already knows. Lead with what they don't: the closest related code, where the change would slot in, or a tradeoff worth flagging. Mention searching only when the result is itself the news (e.g. the capability turns out to be computed internally but never surfaced).

**Apply the project lens** (priority 2 in the system prompt — project excellence outranks individual help). Before replying, ask what the issue reveals beyond this one reporter. If the underlying problem affects many users or the project's health — a false positive on a released artifact, a broken install path, a bad default, a misleading doc — foreground the durable, project-level fix, not just the individual's workaround. Take the pro-project action available to you (open a fix PR, or file/link a tracking issue for the durable fix) rather than handing the reporter only a personal stopgap. Deferring *prioritization* of the durable fix to a maintainer is fine; burying it under personal workarounds is not.

### Reply examples

These examples demonstrate tone, candor, and the boundary between the bot's work and a maintainer's decision. They are neither templates nor a complete list of outcomes. Match the actual issue's context and write the reply afresh.

<example>
<bad reason="The stock politeness carries no result, useful context, or concrete next step">

Bad:

> Thanks for reporting this. I investigated the issue, and a maintainer will review it.

</bad>
<good reason="Each reply acknowledges the person naturally, states the current result, and is honest about what remains">

Good:

**Fix ready**

> Thanks for the clear report. The second retry was dropping the resolved workspace root. #123 keeps it across attempts and adds a regression test; it still needs maintainer review before it lands.

**Reproduction only**

> I could reproduce this, but I don't have a fix I can defend yet. #123 preserves the failure as a regression test; the unresolved part is which layer should own the fallback.

**More information needed**

> I couldn't reproduce this with the configuration in the issue. Could you share the exact command and generated config file? Those are the two inputs that still differ from the failing path.

**Feature request**

> Thanks for spelling out the use case. This isn't available today. `--workspace` selects one root but cannot discover nested roots. The request fits beside that behavior; a maintainer still needs to decide whether discovery should be automatic or opt-in.

</good>
</example>
