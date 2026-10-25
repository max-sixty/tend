# CI failure reports and their causes

When a watched workflow fails on a watched branch, the generated `tend-ci-failures` workflow opens a **report**: an issue by `github-actions[bot]`, labeled `tend-ci-failure`, one per workflow, branch and triggering event. Each later failed run adds a comment, and the next passing run closes the report together with the open sub-issues the bot filed under it. Each comment notifies the bot, which is how a report comes back while the lane stays red.

A report is a queue entry, not a cause. Its session diagnoses the failures and files one **cause issue** per root cause as a sub-issue of the report. Filing a cause issue starts its own triage session, which fixes it like any bug report.

## A report

Load `/tend-ci-runner:ground-claims` before reading the failures.

1. **Read what is new.** This session owes the runs the report and its comments name since the bot's last comment on it, or all of them when it has none. Work from the newest; an older run matters only for a failure the newest doesn't show. A closed report needs nothing.

   ```bash
   gh issue view <report> --json state,body,comments
   gh api "repos/$GITHUB_REPOSITORY/issues/<report>/sub_issues?per_page=100" \
     --jq '.[] | {number, title, state}'
   gh run view <run-id> --json conclusion,jobs,url
   gh run view <run-id> --log-failed  # save it if truncated; read the deciding diagnostic
   ```

2. **Group the failures into causes.** A cause is one mechanism that one fix addresses: several failing tests can share one, and one job can hold several. Establish each current failure per **Claims of recurrence** in `/tend-ci-runner:ground-claims` before matching it to earlier work.

3. **Find each cause's existing owner**, matching evidence per **Claims of recurrence**, not titles or run ids:

   ```bash
   # <term>: a failing test or job name, or a distinctive phrase from the error;
   # search matches titles and bodies, so run one per term
   gh issue list --state open --search "<term>" --json number,title,author --limit 100
   gh pr list --state open --limit 200 --json number,title,headRefName,author
   gh pr list --state closed --search "<term>" --limit 100 --json number,title,state
   # earlier reports for this lane, with their diagnoses and sub-issues
   gh issue list --label tend-ci-failure --state closed --search "<workflow> in:title" \
     --json number,title,closedAt --limit 20
   ```

   Candidates include this report's sub-issues, other reports' sub-issues, and issues and PRs of any author. A closed PR a maintainer rejected ends that cause's work here: read the closure's rationale, and don't re-derive the fix.

4. **Act on each cause.**
   - **An open issue or PR owns it.** Post there only evidence its thread lacks, per `/tend-ci-runner:post-to-github`. An issue under no report becomes a sub-issue of this one (below); an issue has one parent, so one under another report stays there.
   - **A closed cause recurs.** Reopen its issue with the new run as evidence, and attach it here.
   - **Transient**: infrastructure rather than anything the project's code does, such as runner-disk corruption, an isolated network blip or an upstream incident since resolved, with the same path passing on runs around it. File nothing; the report's comment carries the diagnosis, and the lane's next pass closes the report. Earlier transient diagnoses of the same shape are leads, not proof. A reproducible project or upstream defect stays durable even when it first appeared today or some reruns passed, and a cause you can't place as transient is durable.
   - **Nothing owns it.** File a cause issue per `/tend-ci-runner:open-pr`, even for a cause this session could fix: the issue claims the cause for every later run, and its fix gets a session of its own rather than holding up the notification queue. Title it for the failure (`test_merge_order: <short cause>`). Write the body for whoever fixes it: the failing tests or jobs, the deciding error, the runs that show it, the diagnosis so far, and the line `Cause of a failure tracked in #<report>.` Then attach it, and leave the fix to its own session:

     ```bash
     id=$(gh api "repos/$GITHUB_REPOSITORY/issues/<cause>" --jq .id)
     gh api -X POST "repos/$GITHUB_REPOSITORY/issues/<report>/sub_issues" \
       -F sub_issue_id="$id" --silent
     ```

5. **Pick up a stalled cause.** An open sub-issue with no bot comment, no PR referencing it, and no run listed by the active-run check in **Give each thread a current outcome** in `/tend-ci-runner:notifications` never got its session; a run the rate limit refused leaves exactly this. Triage it now, as a cause issue.

6. **Comment on the report only when its map changed**: a cause filed, reopened or attached, a failure matched to another lane's issue, a transient diagnosis. Give each cause its issue and one line of mechanism. When the new runs show only causes already owned, post nothing.

## A cause issue

A cause issue's body names its report. It is a bug report whose reproduction starts from its runs, and triage's steps 3 to 6 apply with these specifics.

- **An existing fix.** Continue the bot's own open PR for the cause per `/tend-ci-runner:continue-pr` rather than opening another. Leave a person's in-flight fix with them, posting only new evidence.
- **Reproduce** with the test commands from the project's instruction files. A failure that no longer reproduces on the default branch's tip, where a later run passes the same test, is resolved: say what resolved it and close the issue.
- **Fix** per `/tend-ci-runner:fix-a-bug`, at the level the cause lives, after searching for the same pattern elsewhere. Before `gh pr create`, follow **Dedup recheck immediately before `gh pr create`** in `/tend-ci-runner:open-pr`. The PR body ends `Closes #<cause> — automated triage`, so the merge closes the cause.
- **Escalate** a cause the bot can't safely fix: one in a system it can't change, one whose fix needs a judgment that isn't the bot's, or one whose attempted fix didn't validate. Leave the issue open and end its comment with the decision needed, addressed with an `@` mention to the person who has to make it: the author of the change that introduced the failure, the person who requested or merged that change where a bot wrote it, or a maintainer. A decision nobody is addressed for stays unmade.
