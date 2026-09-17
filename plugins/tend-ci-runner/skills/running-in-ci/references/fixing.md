# Committing a code fix

Gates on any fix you are about to write, whichever workflow you are running.

- [Reproduce before you fix](#reproduce-before-you-fix)
- [The bar for attempting a fix](#the-bar-for-attempting-a-fix)
- [Shapes that are not a fix](#shapes-that-are-not-a-fix)
- [Fixing the bot's own behavior](#fixing-the-bots-own-behavior)
- [Before the push](#before-the-push)

## Reproduce before you fix

Write a failing test that demonstrates the bug, in the appropriate *existing* test file rather than a new one, and run it to confirm it fails. Use the test commands from the project's instruction files. Where the failure only reproduces in CI, that run is the evidence in its place.

A fix without reproduction evidence is not a conservative fix. If you have neither a failing test nor a failing run — unclear steps, an environment-specific failure — do not write the fix: report what you tried and where it stopped.

If the test passes on the current code, the bug is already fixed. Report that instead.

## The bar for attempting a fix

Attempt a fix only when all four hold:

- the bug reproduces — the failing test or run above
- you understand the root cause
- the fix is localized, 1-3 files
- you are confident it is correct

Fix the root cause, not the symptom.

## Shapes that are not a fix

**A skip guard.** If the change removes coverage for the failing scenario instead of restoring the assertion, stop. Smell patterns: a newly-added early return at the top of the test (`let Ok(_) = X else { return };`, `if !path.exists() { return; }`), a fresh `#[ignore]`, a newly-inserted `skipIf` or `pytest.skip` keyed on the failing condition. The fix belongs in production code or test setup, not in a guard that makes the test bail when the bug fires.

**A test that pins undefined behavior.** When a doc claim and the code disagree and which of the two is wrong is still an open question, the finding *is* that question. A test asserting the current output settles it without the authority to — it turns unspecified behavior into a pinned contract, so the eventual fix arrives looking like a regression. Report the discrepancy and let a maintainer say which side moves; write the test after that.

**A parallel workaround for a root cause already in flight.** A duplicate search catches identical fixes. It misses the *same root cause class, different surface* pattern: several failing tests share one underlying cause, and an outstanding PR fixes some of them but not the one in front of you. When your own analysis names an existing PR as same-root-cause, wait for it to merge and re-run, or mirror its approach for the remaining sites.

## Fixing the bot's own behavior

When the bug is about what the bot did — it didn't use links, it posted the wrong format — the root cause is usually skill compliance rather than missing code. Skills loaded together in a workflow share context, so check every co-loaded skill before adding guidance to one: guidance that already exists in a sibling makes this a compliance problem, and a second copy of it makes the next session's job harder, not easier. `references/skill-pr-workflow.md` covers where a new rule belongs.

## Before the push

Confirm the reproduction test now passes, then review the change per **Review the change before the push** in `references/pushing.md`. That targeted pass, a clean compile, and the review are the local bar. Leave the comprehensive suite to PR CI per **End the turn only when work is shipped** in `SKILL.md`; backgrounding a long suite before pushing risks ending the session while the result is still local.
