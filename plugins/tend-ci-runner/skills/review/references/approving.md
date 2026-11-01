# Approving

- [Before the `APPROVE`](#before-the-approve)
- [After the approval](#after-the-approval)
- [Without the CI wait](#without-the-ci-wait)

## Before the `APPROVE`

Run the approval check against the commit this session reviewed, in the foreground with the longest command timeout the harness allows — a failure showing while other checks still run makes it wait for them:

```bash
tend runtime poll-pr-checks \
  approval <number> "$(cat "$TMPDIR/reviewed-head")"
```

Add the same `--skip` arguments `/tend-ci-runner:monitor-ci` passes the poll, so a check the repo leaves out of the gate can't withhold the approval, and `--no-wait` where **Without the CI wait** applies.

It judges the latest run of each check outside this run and this workflow, and prints one verdict:

- **`approve:`** — post the `APPROVE`. When it lists checks as unverified, those checks settled with no result — cancelled, stale, held for an environment's approval, or a conclusion the script doesn't recognize — so nothing red stands but they did not pass either; name them in the review body.
- **`withhold:`** — a check failed on its own merits. Skip the close-out and finish. If **no prior substantive bot review** stands on this PR, post a brief `COMMENT` stating the diff assessment and the failing check that withholds approval, so a clean dependency bump isn't left with no review signal; an earlier substantive review already stands as the verdict. On a bot PR where you intend to push the fix yourself (**Push fixes**), post that `COMMENT` before pushing, while the checks it names are still the current ones.

Any other exit or command timeout decided nothing: don't approve, and report the approval as unverified.

Re-check the author-readiness gate on the same pass — a comment withholding merge readiness can land after the review began, and the conversation you read under **Pre-flight checks** is by now stale.

## After the approval

Poll the pinned commit to terminal per `/tend-ci-runner:monitor-ci`, then handle the outcome:

- **Green** -> the CI verification is complete.
- **A check failed** and it's related to the PR -> post a follow-up `COMMENT` review with analysis and inline suggestions, then dismiss the bot's approval:
  ```bash
  tend runtime bot-review-state \
    dismiss <number> "CI failed — <reason>"
  ```
  On **human-authored PRs**, do not push fixes — post the analysis and offer to fix, then wait for the author to accept. On **PRs with no human author** (this bot's own, Dependabot, renovate), don't stop at analysis: apply the fix per **Push fixes** so the PR can go green, since no author will act on the offer.
- **A check was cancelled** (conclusion `cancelled`) -> the poll reports it as unverified, not green, and the approval stands: a check that reached no verdict cannot withhold on its merits. Name it as unverified in the closing summary rather than reporting the commit green. A cancellation a rerun replaced at the same SHA is superseded before it reaches that bucket, so one the poll names is a check nothing covered. **Do not re-run cancelled jobs** — that creates another run that gets cancelled again, wasting time in a loop.
- **A check failed** (conclusion `failure`, not `cancelled`) and it's unrelated to the PR changes ->
  1. **Read what the default branch shows.** Check its recent runs of the same workflow for the same failure, and the failed job's log for an infrastructure fault — runner disk, network, an upstream incident. Both decide whether a rerun is worth it and where the failure goes.
  2. **Decide whether a rerun is worth its cost.** A rerun repeats the failed jobs, and this session waits for them: the better part of an hour of runner time on a long suite, a few minutes on a short job. Its result can change two things. It can change the landing, where the repository's CI landing policy (`/tend-ci-runner:merge-pr`) requires green and the repeat might pass. And it can change the attribution, if the repeat shows the PR causes the failure after all; then handle it as the related case above. Weigh how likely the repeat is to pass against what it costs. An infrastructure fault usually passes. A failure that comes and goes on the default branch might. One the default branch has shown on every recent run will most likely recur, and belongs to the default branch's repair whatever the repeat shows. Where the landing policy already lets an unrelated failure land, the repeat can't change the landing. A repeat doesn't settle the cause, since a timing-sensitive test can fail twice running. To rerun, follow **Rerunning failed jobs** in `/tend-ci-runner:monitor-ci`, and route the failure once its result is in.
  3. **Route the failure to its fixer.** Where it came from infrastructure, there is nothing to fix; name it in the closing summary. Where an unsuccessful default-branch run shows the same failure, the default branch's repair owns it: its open CI failure report (labeled `tend-ci-failure`) where one tracks that workflow, or the daily scan in `/tend-ci-runner:review-runs` where none does. Name that run, and the report where there is one, in the closing summary. Otherwise one of the project's own tests fails on this PR while the default branch passes it. Unless the repeat, where you ran one, points at the PR after all, this is a race in the code or in the test, which a rerun hides and no default-branch report ever sees. An issue names the test as flaky in public, so where you chose to rerun, file it only once the repeat's result is in and has been weighed. Search for an open issue about that test. If one exists, append this PR's runs to the bot's comment there, or post one where the bot has none; otherwise open one per `/tend-ci-runner:open-pr`, naming the test, its failure message and the runs, which `tend-triage` takes up to reproduce and fix.

Once the review's remaining work is addressed, complete the landing decision
per `/tend-ci-runner:merge-pr`, using the same reviewed head and its CI evidence.

## Without the CI wait

When the prompt says the review's CI wait is off, the repository has chosen to end a review once its own work is done rather than wait for CI. Judge CI by the checks that have already finished:

- Run the approval check with `--no-wait`. A failure beside checks still running then withholds as it stands, and the check names the ones still running.
- In place of the poll, read the pinned commit once, with the same `--skip` arguments:
  ```bash
  tend runtime poll-pr-checks \
    poll <number> "$(cat "$TMPDIR/reviewed-head")" --no-wait
  ```
  Handle exit 0 or 1 per **After the approval**, except that an unrelated failure is routed without a rerun, since a rerun is a wait. Exit 1 also covers a check that has already failed while others still run: the failure is final, so act on it now, and leave the PR unmerged. Exit 2 (checks still running with none failed, checks that settled without a result, or no complete reading) ends the review: name the checks it lists in the closing summary, and leave the landing decision to a later session or a maintainer, except as below.

Under `yolo`, this review is the lander of a head this bot pushed: a session of this bot that pushes a head hands its landing to the review that follows (**Reviews arriving during verification** in `/tend-ci-runner:push-commits`), so the pushing session's run, even while still going, is not an owner that keeps the head open. That landing doesn't wait on this session either. On exit 2 with checks still running, where everything `/tend-ci-runner:merge-pr` asks of the head but its CI holds — it is the **Reviewed head**, and nothing under **Readiness and ownership** leaves it open — record the head for landing and end:

```bash
tend runtime land-pr \
  intend <number> "$(cat "$TMPDIR/reviewed-head")" "$(cat "$TMPDIR/reviewed-base")"
```

Add the same `--skip` arguments; the record carries them, with the base this review judged, so the landing judges the same checks against the same base. Exit 3 records nothing: the head carries no record of this bot's push (`tend/land/pushed`, which the pushing session made per `/tend-ci-runner:push-commits`), or the PR has moved off that head or base. Leave that landing to a later session or a maintainer. Exit 0 means the record stands, and tend-land takes it from here; the record's own status wakes tend-land, so a check that finished since the review's read isn't missed.

The record is a `tend/land/<number>` status on that exact commit. When every check on it has finished green, tend-land starts a short landing session that makes the landing decision per `/tend-ci-runner:merge-pr`, reading the PR afresh for holds, findings, the approval and ownership. A red result on a PR with no human author starts a session that continues its repair per `/tend-ci-runner:continue-pr`. Where the review can't record the head (a draft, a hold, an unresolved finding), the landing decision passes to a later session or a maintainer, as it does on a head someone else pushed.

A fix this session pushes is covered the same way: record the push (**After the push** in `/tend-ci-runner:push-commits`), and the run queued behind this one reviews and records the pushed head, so end once the push is made rather than polling it.
