# Approving

Depth behind `/tend-ci-runner:review`'s **Submit** and **Monitor CI** steps: read before an empty-body APPROVE, and after one to handle the CI outcome.

- [Before the APPROVE](#before-the-approve)
- [After the approval](#after-the-approval)

## Before the APPROVE

**Before APPROVE specifically**, run the snapshot below and require its `head_sha` to equal `$TMPDIR/reviewed-head`. The rollup is pinned to `$TMPDIR/reviewed-head`; a head mismatch means it does not cover the live head, so post findings if you have them, otherwise finish and leave the approval to the queued run. Then inspect that rollup: if any check has reached terminal `FAILURE`, do not emit an empty-body APPROVE — the close-out reads as the bot rubber-stamping over the visibly red signal. Re-check the author-readiness gate on the same pass — a comment withholding merge readiness can land after the review began, and the conversation you read under **Pre-flight checks** is by now stale.

An approval you post at a re-targeted head is yours to stand behind: the queued run reads that head as reviewed and finishes, so no successor session dismisses the approval if a check goes red. **Monitor CI**'s poll is the whole net — run it to terminal before ending the session.

Reduce the rollup to the **latest entry per check name and workflow** before reading it. When a concurrency-cancelled run is replaced, GitHub keeps *both* check runs on the commit, so an un-deduped scan reports the superseded `FAILURE` alongside the replacement's `SUCCESS` — and keeps reporting it forever. Key on `workflowName` as well as the name: two workflows can register the same check name, and collapsing those into one entry would hide a genuine red behind an unrelated green.

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/poll_pr_checks.py" \
  snapshot <number> "$(cat "$TMPDIR/reviewed-head")"
```

The JSON reports `head_sha`, `pending`, and `failed` for the pinned commit after
dropping this run, this workflow, and superseded check runs. When those filters
leave no external contexts, both lists are empty; that is the clean `failed`
empty branch for this pre-approval snapshot, not evidence that the repository
registered a gating check. Re-run the snapshot after any poll; no shell state
survives between calls.

**Don't treat a mid-flight rollup as settled.** A `FAILURE` co-existing with a non-empty `pending` list is often a *stale cancellation-cascade* artifact, not a real failure: when several events fire near-simultaneously (e.g. Dependabot opening a PR), the `tests` concurrency group cancels all but the latest, and a cancelled contributor makes an `if: always()` merge-gate omnibus (like PRQL's `check-ok-to-merge`) resolve to conclusion `FAILURE` — *not* `cancelled`, so it slips past the post-approve cancellation awareness below and reads as red. A fresh replacement run is already in flight and will re-register the omnibus. So decide on the **settled** rollup:

- **`failed` non-empty and `pending` non-empty** — the rollup hasn't settled. Foreground-poll until non-own checks are terminal (the loop in `/tend-ci-runner:running-in-ci`'s `references/ci-monitoring.md`), then re-run the snapshot. Judge the settled state, not the mid-flight snapshot — a stale cancellation-cascade `FAILURE` drops out once the replacement omnibus registers, but *only* via the reduction above; the superseded check run itself never leaves the commit.
- **`failed` non-empty and the poll cap expired with `pending` non-empty** — settlement is out of reach this session; a release or nightly matrix routinely outlasts the cap. Re-run the snapshot first. Then decide on **provenance**, not on settlement: resolve each remaining failure URL to its run and read that run's own conclusion.

  For each snapshot URL containing `/actions/runs/<run-id>/`, inspect that run:

  ```bash
  gh run view <run-id> --json conclusion --jq '.conclusion'
  ```

  A URL without an Actions run id is unresolved third-party status, not a
  cancellation you can prove.

  Every one `cancelled` — the red is superseded, so APPROVE and name the still-unverified checks in the body. `cancelled` is the only conclusion that earns an approval here: a real `failure`, an empty conclusion (the run is still going, so the job failed on its own merits), or an unresolvable URL (a third-party status context like `codecov/patch`, never an Actions run) all take the terminal-red branch below. Don't leave this to improvisation: the same stale red must not draw an APPROVE on one PR and a withheld approval on the next.
- **`failed` non-empty and `pending` empty** — genuine terminal red. Skip the close-out and finish. But if **no prior substantive bot review** stands on this PR, don't exit fully silent or leave only a `+1` reaction — a clean external-dependency bump then carries zero review signal. Post a brief COMMENT stating the diff assessment and the failing check that withholds approval. Any earlier substantive review already stands as the active verdict — leave it. On a bot PR where you intend to push the fix yourself (**Push fixes**), post that COMMENT before pushing, while the rollup it describes is still the current one.
- **`failed` empty** — proceed with APPROVE.

**Monitor CI**'s "approve, foreground-poll CI, dismiss if a check fails" pattern only recovers while the session is still alive — the job timeout or a poll cap can leave a post-approve failure undismissed and the PR carrying a misleading APPROVED state. A synchronous pre-APPROVE peek catches the case where the failure is already in the rollup — including non-required checks like `codecov/patch` that an overlay treats as a merge gate. Reducing to the latest entry per name and workflow — and, when the cap expires first, checking each `FAILURE`'s run conclusion — is what keeps a superseded red from being mistaken for a real one.

## After the approval

Poll the pinned commit to terminal per `/tend-ci-runner:running-in-ci`'s `references/ci-monitoring.md`, then handle the outcome:

- **All required checks passed** -> done.
- **A check failed** and it's related to the PR -> post a follow-up COMMENT review with analysis and inline suggestions, then dismiss the bot's approval:
  ```bash
  uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" \
    dismiss <number> "CI failed — <reason>"
  ```
  On **human-authored PRs**, do not push fixes — post the analysis and offer to fix, then wait for the author to accept. On **PRs with no human author** (this bot's own, Dependabot, renovate), don't stop at analysis: apply the fix per **Push fixes** so the PR can go green, since no author will act on the offer.
- **A check was cancelled** (conclusion `cancelled`) -> do nothing. Cancellations are almost always caused by concurrency groups — a new workflow run (often triggered by your own approval event) replaces the in-progress one. The replacement run will cover the cancelled checks. **Do not re-run cancelled jobs** — that creates another run that gets cancelled again, wasting time in a loop.
- **A check failed** (conclusion `failure`, not `cancelled`) and it's a transient flake (unrelated to the PR changes) ->
  1. **Re-run the failed jobs:**
     ```bash
     gh run rerun <run-id> --failed
     ```
  2. **Report the flake.** Search for an open issue about the specific flaky test. If found, append to an existing bot comment rather than posting a new one.
