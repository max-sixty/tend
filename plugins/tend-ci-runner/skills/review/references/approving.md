# Approving

- [Before the `APPROVE`](#before-the-approve)
- [After the approval](#after-the-approval)

## Before the `APPROVE`

Run the approval check against the commit this session reviewed, in the foreground with the longest command timeout the harness allows — a failure showing while other checks still run makes it wait for them:

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/poll_pr_checks.py" \
  approval <number> "$(cat "$TMPDIR/reviewed-head")"
```

Add the same `--skip` arguments `/tend-ci-runner:monitor-ci` passes the poll, so a check the repo leaves out of the gate can't withhold the approval.

It judges the latest run of each check outside this run and this workflow, and prints one verdict:

- **`approve:`** — post the `APPROVE`. When it lists checks as unverified, those checks settled with no result — cancelled, stale, held for an environment's approval, or a conclusion the script doesn't recognize — so nothing red stands but they did not pass either; name them in the review body.
- **`withhold:`** — a check failed on its own merits. Skip the close-out and finish. If **no prior substantive bot review** stands on this PR, post a brief `COMMENT` stating the diff assessment and the failing check that withholds approval, so a clean dependency bump isn't left with no review signal; an earlier substantive review already stands as the verdict. On a bot PR where you intend to push the fix yourself (**Push fixes**), post that `COMMENT` before pushing, while the checks it names are still the current ones.

Any other exit or command timeout decided nothing: don't approve, and report the approval as unverified.

Re-check the author-readiness gate on the same pass — a comment withholding merge readiness can land after the review began, and the conversation you read under **Pre-flight checks** is by now stale.

An approval you post at a re-targeted head is yours to stand behind: the queued run reads that head as reviewed and finishes, so no successor session dismisses the approval if a check goes red. **Monitor CI**'s poll is the whole net — run it to terminal before ending the session.

## After the approval

Poll the pinned commit to terminal per `/tend-ci-runner:monitor-ci`, then handle the outcome:

- **All required checks passed** -> done.
- **A check failed** and it's related to the PR -> post a follow-up `COMMENT` review with analysis and inline suggestions, then dismiss the bot's approval:
  ```bash
  uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" \
    dismiss <number> "CI failed — <reason>"
  ```
  On **human-authored PRs**, do not push fixes — post the analysis and offer to fix, then wait for the author to accept. On **PRs with no human author** (this bot's own, Dependabot, renovate), don't stop at analysis: apply the fix per **Push fixes** so the PR can go green, since no author will act on the offer.
- **A check was cancelled** (conclusion `cancelled`) -> the poll reports it as unverified, not green, and the approval stands: a check that reached no verdict cannot withhold on its merits. Name it as unverified in the closing summary rather than reporting the commit green. A cancellation a rerun replaced at the same SHA is superseded before it reaches that bucket, so one the poll names is a check nothing covered. **Do not re-run cancelled jobs** — that creates another run that gets cancelled again, wasting time in a loop.
- **A check failed** (conclusion `failure`, not `cancelled`) and it's unrelated to the PR changes ->
  1. **Re-run the failed jobs** and wait for them, per **Rerunning failed jobs** in `/tend-ci-runner:monitor-ci`. A failure that repeats is not intermittent: either the PR causes it after all (the case above), or the base carries it, which `/tend-ci-runner:ci-fix` answers on the default branch; name that in the closing summary.
  2. **Route a test that failed and then passed to a fix.** Where the failure came from infrastructure — runner disk, network, an upstream incident — there is nothing to fix; name it in the closing summary. Otherwise one of the project's own tests failed on a commit where it also passes: a race in the code or in the test, which a rerun hides and `ci-fix` never sees. Search for an open issue about that test. If one exists, append this run to the bot's comment there, or post one where the bot has none; otherwise open one per `/tend-ci-runner:open-pr`, naming the test, its failure message and the run, which `tend-triage` takes up to reproduce and fix.
