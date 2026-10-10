---
name: triage
description: Triages a newly opened GitHub issue — classifies, reproduces bugs, attempts conservative fixes, and comments.
argument-hint: "[issue number]"
metadata:
  internal: true
---

# Issue Triage

Triage a newly opened GitHub issue.

**Issue to triage:** $ARGUMENTS

## Step 1: Load required skills

- Load `/tend-ci-runner:run-tend` first, including its repo-specific overlay.
- Load `/tend-ci-runner:post-to-github` before composing a comment or PR body.
- Load `/tend-ci-runner:fix-a-bug` before attempting a fix.
- Load `/tend-ci-runner:push-commits` before pushing, `/tend-ci-runner:open-pr` before opening a PR, and `/tend-ci-runner:monitor-ci` after pushing a fix.

Reproduce before fixing, find evidence before speculating, and test before committing.

## Step 2: Read and classify the issue

```bash
gh issue view $ARGUMENTS --json title,body,labels,author
# The reporter's relationship to the repository, read by steps 6 and 7
gh api "repos/$GITHUB_REPOSITORY/issues/$ARGUMENTS" --jq '.author_association'
```

An issue the bot itself opened — a nightly failure, a CI report, a code-quality finding — is a report to act on, not a self-conversation: the system prompt's self-loop guard covers the bot's own *comments*, and triage runs on these normally while no bot comment answers them yet.

One exception: a tracker the bot maintains for its own evidence — its body says **Do not close manually**, and later runs append to it — carries no report. End the run without commenting.

Classify into:

- **Bug report** — describes unexpected behavior, includes steps to reproduce or error output. Descriptions of changed behavior ("no longer works", "used to work") strongly signal a bug even with a terse body, and so does current behavior that gives a wrong or confusing result, even when the report asks for an enhancement.
- **Feature request** — asks for new functionality or behavior changes with a concrete expected behavior
- **Idea** — an open-ended proposal or direction to explore, without a settled behavior to build
- **Question** — asks how to do something or how something works
- **Other** — doesn't fit the above categories

## Step 3: Check for duplicates

*Skip for questions and other.*

```bash
# Search open issues for similar problems. <keywords>: the symptom, error
# text, or component the issue names, and the words another reporter would
# use for it; search matches titles and bodies, so run one per term
gh issue list --state open --search "<keywords>" --json number,title,labels --limit 100

# Check for existing fix branches and PRs
git branch -r --list 'origin/fix/*'
gh pr list --state open --json number,title,headRefName --limit 200
```

If a duplicate or existing fix is found, note it for the comment in step 7. Don't create a duplicate fix.

## Step 4: Investigate existing functionality

*Feature requests and ideas.*

Search the codebase to check whether the requested feature already exists.

1. **Extract the core ask** — What specific behavior does the requester want?
2. **Search for implementations** — Grep for relevant function names, config keys, CLI flags, and domain terms.
3. **Read key files** — If searches find hits, read the relevant source to understand what already exists and how it works.
4. **Check docs and help text** — Look for user-facing documentation of the feature.

Record what you found (or didn't find) for use in steps 6 and 7.

## Step 5: Reproduce the bug

*Bug reports only.*

Follow **Reproduce first** in `/tend-ci-runner:fix-a-bug`: a failing test in an existing test file, run to confirm it fails.

If you cannot reproduce the bug (unclear steps, environment-specific, etc.), note what you tried and skip to step 7. If the test passes, the bug may already be fixed — note that for the comment.

## Step 6: Fix (conservative)

*Any issue where a PR is reasonably likely to be merged.*

Open a PR when a maintainer is reasonably likely to merge it; otherwise report the finding in Step 7. Weigh at least:

- **What the issue is.** A reproduced bug usually clears the bar. A feature request can, when the repository's docs, tests, or neighbouring code settle how the behavior should work. An idea, or a request that leaves a product choice open, usually doesn't.
- **Who is asking**, from the `author_association` read in Step 2 and the reporter's history in the repository. A maintainer's or regular contributor's request carries the project's direction; a feature request from a new user usually waits for a maintainer to endorse it. An issue the bot opened carries no maintainer direction: its `author_association` reflects the bot's write access.

The change must also be verifiable. For a feature, a test of the new behavior that fails before the change, or a check the repository already runs over the edited files, stands in for the reproduction in `/tend-ci-runner:fix-a-bug`'s gates. Title a feature's commit and PR `feat:` rather than `fix:`.

`/tend-ci-runner:fix-a-bug` carries the gates: the reproduction gate, the conditions a fix attempt needs, skill-text fixes, the shapes of bad fix, and the local bar before pushing. Read it before writing any fix. Where a gate fails, go to Step 7 and report the outcome you established.

### If fixing

1. Clear the local bar in `/tend-ci-runner:fix-a-bug`.
2. Create branch, commit, push, and create PR:
   ```bash
   git checkout -b fix/issue-$ARGUMENTS
   git add -A
   # <trailer>: `Closes #$ARGUMENTS` where the fix settles the whole report,
   # `Refs #$ARGUMENTS` where it settles part of one.
   git commit -m "fix: <description>

   <trailer>"
   git push -u origin fix/issue-$ARGUMENTS
   ```
   Compose the body at `$TMPDIR/pr-body.md`. Write for a maintainer deciding whether the current fix resolves the issue: explain the causal finding, the resulting behavior change, and the reproduction test that now passes. Follow **Reader-facing prose** in `/tend-ci-runner:run-tend`, and end with the commit's trailer plus ` — automated triage`: `Closes #$ARGUMENTS — automated triage` where the fix settles the whole report, `Refs #$ARGUMENTS — automated triage` where it settles part of one, with the body naming what it leaves. See **Closing keywords** in `/tend-ci-runner:open-pr`.

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
   <good reason="A partial fix names what it leaves and references the issue instead of closing it">

   Good, where the fix settles part of the report:

   ```markdown
   A retry dropped the resolved workspace root, so its second attempt read from the process directory and failed outside the repository. Retry state now keeps the root, and the regression test covers the second attempt. The report's other half — the retry budget resetting between attempts — runs through a different code path and is untouched here.

   Refs #123 — automated triage
   ```

   </good>
   </example>

   ```bash
   gh pr create --title "fix: <description>" --body-file "$TMPDIR/pr-body.md"
   ```
3. Wait for CI per `/tend-ci-runner:monitor-ci`.

### If reproduction test works but fix is not confident

Commit just the failing test on a reproduction branch and open a PR:

```bash
git checkout -b repro/issue-$ARGUMENTS
git add -A
git commit -m "test: add reproduction for #$ARGUMENTS"
git push -u origin repro/issue-$ARGUMENTS
```

Compose the body at `$TMPDIR/pr-body.md`. Make clear that the PR deliberately adds a failing reproduction without a fix, what behavior it captures, and any causal boundary already established so a maintainer knows what remains to decide. Follow **Reader-facing prose** in `/tend-ci-runner:run-tend`, and end with `Automated triage for #$ARGUMENTS`.

```bash
gh pr create --title "test: reproduction for #$ARGUMENTS" --body-file "$TMPDIR/pr-body.md"
```

Note the PR number for the comment.

## Step 7: Comment on the issue

Re-fetch before posting, per **Recheck before posting** in `/tend-ci-runner:post-to-github` — triage can take minutes, so re-fetch the issue and skip any point a new human comment or a sibling tend workflow already covered.

Always comment via `gh issue comment`. Write for the issue author: lead with the current disposition, then give the causal finding and the action taken or the one concrete input or decision still needed. Link any fix, reproduction, or duplicate. Follow **Reader-facing prose** in `/tend-ci-runner:run-tend`; do not restate the report or narrate the investigation. Report the fix's verified CI and landing disposition; opening a fix alone does not establish resolution. Acknowledge the reporter when the situation calls for it, but do not use thanks or maintainer deferrals as fixed openers and closers. Do not present the bot's judgment as a maintainer decision.

**Stay within what you verified.** State facts you found in the codebase — don't characterize something as "known" unless you find prior issues or documentation about it. Don't speculate beyond the code you read.

**Report the finding, not the search.** A feature request already tells you the capability is missing, so neither "this isn't available today" nor "I searched and didn't find an existing implementation" is news to the requester. Show you understood the ask by building on it, not by restating it: lead with the closest related code, where the change would slot in, or a tradeoff worth flagging. Say something is missing only when that is itself the news — the requester seems to believe a feature exists that doesn't, part of the request already works, or the capability is computed internally but never surfaced.

**Apply the project lens** (priority 2 in the system prompt — project excellence outranks individual help). Before replying, ask what the issue reveals beyond this one reporter. If the underlying problem affects many users or the project's health — a false positive on a released artifact, a broken install path, a bad default, a misleading doc — foreground the durable, project-level fix, not just the individual's workaround. Take the pro-project action available to you (open a fix PR, or file/link a tracking issue for the durable fix) rather than handing the reporter only a personal stopgap. Deferring *prioritization* of the durable fix to a maintainer is fine; burying it under personal workarounds is not.

### Reply examples

These examples demonstrate tone, candor, and how to report the verified outcome or remaining decision. They are neither templates nor a complete list of outcomes. Match the actual issue's context and write the reply afresh.

<example>
<bad reason="The stock politeness carries no result, useful context, or concrete next step">

Bad:

> Thanks for reporting this. I investigated the issue, and a maintainer will review it.

</bad>
<good reason="Each reply acknowledges the person naturally, states the current result, and is honest about what remains">

Good:

**Fix merged**

> Thanks for the clear report. The second retry was dropping the resolved workspace root. #123 merged the fix and a regression test that fails on the old code and passes with the correction.

**Reproduction only**

> I could reproduce this, but I don't have a fix I can defend yet. #123 preserves the failure as a regression test; the unresolved part is which layer should own the fallback.

**More information needed**

> I couldn't reproduce this with the configuration in the issue. Could you share the exact command and generated config file? Those are the two inputs that still differ from the failing path.

**Feature request**

> `--workspace` already selects a single root, so nested discovery would build on it. The open question is whether discovery should run automatically or only when asked for, and that is a maintainer decision.

</good>
</example>
