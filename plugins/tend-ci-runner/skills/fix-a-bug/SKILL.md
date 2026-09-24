---
name: fix-a-bug
description: Write a code fix for a bug. Use before writing any fix, whichever workflow you are running.
metadata:
  internal: true
---

# Fixing a bug

The gates every fix attempt clears, whichever workflow you are running.

## Required skills

- Load `/tend-ci-runner:propose-instructions` if the fix edits a skill or instruction file.
- Load `/tend-ci-runner:push-commits` before reviewing the fix for a push.

## Reproduce first

1. **Understand the report** — What command was run? What was expected? What actually happened?
2. **Find relevant code** — Search the codebase for the functionality described.
3. **Write a failing test** — Add a test to the appropriate *existing* test file that demonstrates the bug. Don't create new test files.
4. **Run the test** to confirm it fails. Use the test commands from the project's instruction files.

Generally, have a failing test before writing a fix. A fix without reproduction evidence is not a conservative fix: report what you established instead. If the test passes, the bug may already be fixed — say so.

**Only attempt a fix if all of these hold:**

- Bug is clearly reproducible (usually, the test written above fails)
- Root cause is understood
- Fix is localized (1-3 files changed)
- Confident the fix is correct

## Skill text fixes

When the bug is about bot behavior (e.g., "bot didn't use links", "bot posted wrong format"), the root cause is often a skill/prompt compliance issue, not missing code. Before adding an instruction to a skill:

1. **Check ALL co-loaded skills** — Skills loaded together in the same workflow share context. If the instruction already exists in a co-loaded skill, the issue is behavioral compliance, not a missing instruction.
2. **Don't duplicate instructions across skills.**

Instructions themselves go through `/tend-ci-runner:propose-instructions`, which covers where the rule lands and what it leaves out.

## Don't "fix" tests by adding skip guards

If the proposed change removes coverage for the failing scenario instead of restoring the assertion, stop. Smell patterns: a newly-added early-return at the top of the test (`let Ok(_) = X else { return };`, `if !path.exists() { return; }`), a fresh `#[ignore]`, a newly-inserted `skipIf` / `pytest.skip` keyed on the failing condition. The fix belongs in production code or test setup, not in a guard that makes the test bail when the bug fires.

## Don't pin undefined behavior in a test

When a doc claim and the code disagree and which of the two is wrong is still an open question, the finding *is* that question. A test asserting the current output settles it without the authority to — it turns unspecified behavior into a pinned contract, so the eventual fix arrives looking like a regression. Report the discrepancy and let a maintainer say which side moves; write the test after that.

## Defer to in-flight same-root-cause PRs

A duplicate search catches identical fixes. It misses the *same root cause class, different surface* pattern: several failing tests share one underlying cause, and an outstanding PR fixes some of them but not the one in front of you. When your own analysis names an existing PR as same-root-cause, that's the signal to wait for it to merge and re-run, or to mirror its approach for the remaining sites — not to open a parallel narrow workaround.

## The local bar

Fix the root cause, not the symptom. Confirm the reproduction test now passes, then review the change per **Review the change before the push** in `/tend-ci-runner:push-commits`. That targeted pass, a clean compile, and the review are the local bar. Leave the comprehensive suite to PR CI per `/tend-ci-runner:run-tend`'s "End the turn only when work is shipped"; backgrounding a long suite before push risks ending the session while the result is still local.
