# CI failure reports and their causes

When a watched workflow fails on a watched branch, the generated `tend-ci-failures` workflow opens a **report** with the bot token: an issue labeled `tend-ci-failure`, one per workflow, branch and triggering event. Opening it starts ordinary issue triage. Each later failed run adds a comment without starting a session; `/tend-ci-runner:review-runs` checks for unhandled failures. The next passing run closes the report; its linked cause issues stay open until their own sessions close them.

A report is a queue entry, not a cause. Its session diagnoses the failures and files one ordinary **cause issue** per root cause, linking it in the report's diagnosis. A cause can affect several reports; these links carry the evidence without a parent hierarchy or its child limit. Filing a cause issue starts its own triage session, which fixes it like any bug report. A cause no session is working, because its run was refused or its lane went quiet, is the daily `/tend-ci-runner:review-runs` sweep's to re-dispatch; a report session leaves it alone.

## A report

Load `/tend-ci-runner:ground-claims` before reading the failures.

1. **Read what is new.** This session owes the runs the report and its comments name since the last triage diagnosis, or all of them when it has none. Automated run-record comments are evidence, not diagnoses, even though both are authored by the bot. Work from the newest; an older run matters only for a failure the newest doesn't show. A closed report needs nothing.

   ```bash
   gh issue view <report> --json state,body,comments
   gh run view <run-id> --json conclusion,jobs,url
   gh run view <run-id> --log-failed  # save it if truncated; read the deciding diagnostic
   ```

2. **Group the failures into causes.** A cause is one mechanism that one fix addresses: several failing tests can share one, and one job can hold several. Establish each current failure per **Claims of recurrence** in `/tend-ci-runner:ground-claims` before matching it to earlier work.

3. **Find each cause's existing owner.** Read the shared inventories once for this diagnosis and reuse them across causes, updating them as this session files or links work:

   ```bash
   gh pr list --state open --limit 200 --json number,title,headRefName,author
   # earlier reports for this lane, with their diagnoses and linked causes
   gh issue list --label tend-ci-failure --state closed --search "<workflow> in:title" \
     --json number,title,closedAt --limit 20
   ```

   For each cause, match evidence per **Claims of recurrence**, not titles or run ids. Search for owners the shared inventories do not identify:

   ```bash
   # <term>: a failing test or job name, or a distinctive phrase from the error;
   # search matches titles and bodies, so run one per term
   gh issue list --state all --search "<term>" --json number,title,state,author --limit 100
   gh pr list --state closed --search "<term>" --limit 100 --json number,title,state
   ```

   Candidates include causes linked in this or other reports' diagnoses, and issues and PRs of any author. A closed PR a maintainer rejected ends that cause's work here: read the closure's rationale, and don't re-derive the fix.

4. **Act on each cause.**
   - **An open issue or PR owns it.** Post there only evidence its thread lacks, per `/tend-ci-runner:post-to-github`.
   - **A closed cause recurs.** Reopen its issue with the new run as evidence.
   - **Transient**: infrastructure rather than anything the project's code does, such as runner-disk corruption, an isolated network blip or an upstream incident since resolved, with the same path passing on runs around it. File nothing; the report's comment carries the diagnosis, and the lane's next pass closes the report. Earlier transient diagnoses of the same shape are leads, not proof. A reproducible project or upstream defect stays durable even when it first appeared today or some reruns passed, and a cause you can't place as transient is durable.
   - **Nothing owns it.** File a cause issue per `/tend-ci-runner:open-pr`, even for a cause this session could fix: the issue claims the cause for every later run, and its fix gets a session of its own rather than holding up the notification queue. Title it for the failure (`test_merge_order: <short cause>`), and label it `tend-ci-cause`: the label marks it as a cause issue, and the rate limit's issue burst count leaves it out. Write the body for whoever fixes it: the failing tests or jobs, the deciding error, the runs that show it, the diagnosis so far, and the line `Cause of a failure tracked in #<report>.` Leave the fix to its own session:

     ```bash
     gh label create tend-ci-cause --color fbca04 \
       --description "A root cause of a CI failure report" 2>/dev/null || true
     gh issue create --label tend-ci-cause --title "<title>" --body-file "$TMPDIR/cause.md"
     ```

5. **Comment on the report only when its map changed**: a cause filed or reopened, a failure matched to an owner not yet linked from this report, a transient diagnosis. Give each cause its issue and one line of mechanism. When the new runs show only causes already linked here, post nothing.

## A cause issue

A cause issue is labeled `tend-ci-cause`, and its body names its report. It is a bug report whose reproduction starts from its runs, and triage's steps 3 to 6 apply with these specifics.

- **An existing fix.** Follow **Share ownership of a proven cause** in `/tend-ci-runner:fix-a-bug` when evidence connects this issue to other reports or an existing repair.
- **Reproduce** with the test commands from the project's instruction files. Close a cause when evidence establishes its resolution on the default branch, verifying the affected path against the repair or recovered incident. A passing rerun alone does not settle an intermittent failure.
- **Fix** per `/tend-ci-runner:fix-a-bug`, at the level the cause lives, after searching for the same pattern elsewhere. Before `gh pr create`, follow **Dedup recheck immediately before `gh pr create`** in `/tend-ci-runner:open-pr`. The PR body ends `Closes #<cause> — automated triage`, so the merge closes the cause.
- **Unfinished diagnosis or verification.** Follow **Retain engineering ownership** in `/tend-ci-runner:fix-a-bug`.
