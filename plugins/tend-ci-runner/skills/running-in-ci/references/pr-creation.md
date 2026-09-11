# Opening PRs and issues

Depth behind `/tend-ci-runner:running-in-ci`: read before `gh pr create` or `gh issue create`, and before editing a PR's title or description.

- [Filing issues in this repo](#filing-issues-in-this-repo)
- [PR creation](#pr-creation)
- [Atomic PRs](#atomic-prs)
- [Keeping PR titles and descriptions current](#keeping-pr-titles-and-descriptions-current)

## Filing issues in this repo

An issue here is not a note to a maintainer — where `tend-triage` is enabled (the default), it fires on `issues` and does the work. Filing one for a fix you have already scoped hands your own analysis to a second agent run, which re-derives it from your issue body and opens the PR minutes later at full session cost, on a thread nobody needed.

So if you can open the PR in this run, open the PR. Reserve an issue for what you genuinely can't finish here: a problem too large or ambiguous to fix, one that needs a maintainer decision, or one whose verification is out of reach from CI. Bookkeeping issues are a separate case, not this trade-off: `ci-fix`'s transient-diagnosis tracker carries `tend-outage`, which the generated `tend-triage` and `tend-mention` `if:` skip, so no conversion run fires.

This governs your own repo only; filing into another repo follows **Other Repos** in `SKILL.md`.

## PR creation

When asked to create a PR, use `gh pr create` directly.

Before creating a branch or PR, check for existing work:

```bash
gh pr list --state open --limit 200 --json number,title,headRefName --jq '.[] | "#\(.number) [\(.headRefName)]: \(.title)"'
git branch -r --list 'origin/fix/*'
```

Open PRs compete for one maintainer's attention. A self-initiated improvement — a sweep finding, a skill or workflow refinement nobody asked for — draws on a budget: when the bot already has five or more PRs open (`gh pr list --state open --author "@me"`), open one only for a wrong outward action (see **Weighing a Fix** in `SKILL.md`), and hold the rest until the queue drains, recorded where the maintainer will see it (the evidence store, or a line on the triggering thread) rather than as an issue (**Filing Issues in This Repo** explains why not). The budget never holds work someone asked for, a fix a user is waiting on (a red default branch, a triaged bug), or the scheduled maintenance a skill itself instructs (a workflow regeneration, a pinned-version bump, a data refresh). Base every PR on the default branch; never stack one on an unmerged bot branch, which puts the same change through review once per link in the chain.

Write PR titles, issue titles, and commit subjects in plain, literal language that a reader can understand without the body. Name the concrete component and behavior changed while keeping any prefix the repository requires. Put the explanation in the body. The test: someone who has not read the diff can say what changed. Example: `Stop worker retries after cancellation`.

The titles that fail it read as figures rather than descriptions — a metaphor, a subject withheld for effect, a phrase that only lands once you already know the bug. Rewrite to the literal statement: `Press again for the tab the driver lost, not the one Chromium never made` → `Retry the click when the browser driver never reports the opened tab`.

Describe the current PR for a maintainer deciding whether to merge it. Follow **Reader-facing prose** in `SKILL.md` and synthesize across commits and review rounds.

If an existing PR addresses the same problem, work on that PR instead.

### Configure git identity before the first commit

Runners don't always pre-seed a git identity, and a fresh `git worktree` never inherits one. Without it `git commit` fails with `Author identity unknown`, the branch gets pushed with **no commit**, and `gh pr create` then fails with `No commits between main and <branch>`. Set it once before your first commit — `--global` covers the main checkout and every `$TMPDIR` worktree in one shot, and it's idempotent, so re-running is safe:

```bash
BOT_LOGIN=$(gh api user --jq '.login')
BOT_ID=$(gh api user --jq '.id')
git config --global user.name "$BOT_LOGIN"
git config --global user.email "${BOT_ID}+${BOT_LOGIN}@users.noreply.github.com"
```

The noreply form (`<id>+<login>@users.noreply.github.com`) keeps commits attributed to the bot account and passes `verified`-email push rules.

### Dedup recheck immediately before `gh pr create`

A separate mention on a different issue/PR can trigger a concurrent run asking for the same fix. Those runs are not serialized — each has its own concurrency group — so both may read an empty `gh pr list` at session start and then each open their own PR minutes later, producing near-duplicates. A long workflow queue (`tend-mention` can wait hours) also lets a sibling run open *and merge* a PR before this run starts — already-merged duplicates need to be in scope too. Re-run the check **as the last step before `gh pr create`**, with `--state all` so closed and merged siblings show up:

```bash
BOT_LOGIN=$(gh api user --jq '.login')
gh pr list --state all --author "$BOT_LOGIN" --limit 200 \
  --json number,title,state,mergedAt,headRefName,createdAt
```

When the trigger is an issue/PR comment, also search for sibling PRs that reference that issue number — a merged PR's title or body often cites the issue (`Fixes #123`, `#123` in title) even when the branch name diverged:

```bash
gh pr list --state all --search "author:$BOT_LOGIN <issue-number>" \
  --json number,title,state,mergedAt
```

Compare by title keywords **and** the files the new PR would modify — two concurrent fixes for the same bug typically pick different branch names, so a branch-name match is not sufficient. If a sibling bot PR overlaps in scope — whether open, closed, or already merged — **do not create**: post a comment on the triggering thread linking the existing PR and exit.

A fix may have landed directly on the default branch while you worked. Fetch it immediately before creating the PR:

```bash
DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')
git fetch origin "$DEFAULT_BRANCH"
git log --oneline "HEAD..origin/$DEFAULT_BRANCH"
```

Inspect overlapping commits and reproduce the problem on the fetched default branch. If it is fixed, don't create the PR; comment on the triggering thread when it needs a response.

### Fetch the prior rejection before re-deriving a fix

A change a maintainer already turned down leaves its verdict in two places the checks above don't fetch: the closed PR that carried it, and the comments on the issue tracking it. Search by the symbol or path the change would edit — a finding re-derived from the code has no issue number, and an attempt predating the tracking issue cites none either — then read the closed hits and the issue bodies, not just their titles.

Search, don't scan. A recency-ordered listing ages a rejection out in bot-throughput time: at a few PRs a day, any `--limit` drops it within weeks, and raising the cap only moves that boundary. A symbol match stays small however many PRs have landed since.

```bash
BOT_LOGIN=$(gh api user --jq '.login')
# <symbol>: the function, file, or config key the change would edit
gh pr list --state all --search "author:$BOT_LOGIN <symbol>" --limit 100 --json number,title,state,closedAt
gh pr view <n> --json comments,reviews --jq '[.comments[].body, .reviews[].body]'
gh issue view <n> --json body,comments --jq '[.body, .comments[].body]'
```

What you find governs: a PR closed on the **code** leaves the fix available to redo, while one closed on the **approach** means the semantics are still an open maintainer question — add findings to that thread rather than opening a second implementation of it.

## Atomic PRs

Split unrelated changes into separate PRs — one concern per PR. If one change could be reverted without affecting the other, they belong in separate PRs.

## Keeping PR titles and descriptions current

When review changes the approach, recompose the title and description around the current result rather than appending a history of the changes:

```bash
gh api repos/{owner}/{repo}/pulls/{number} -X PATCH \
  -f title="new title" -F body=@"$TMPDIR/updated-body.md"
```

**A description describes the whole PR, not the increment this run reviewed.** It presents the current result coherently; prior attempts and review rounds stay in the thread unless they remain relevant to the merge decision. Scope every behavior claim in it to the PR's merge base, not whatever range this run happened to diff:

```bash
gh pr diff <number>   # merge-base→head, whatever this session has checked out
```

On a long-lived branch those are different commits — nightly's rolling `tend/update-workflows` PR accumulates a release per run, so its head is several releases past its merge base — and a claim that is true of the last increment can be false of the PR. If you can only verify the increment, name the base the claim is against instead of writing it as a claim about the PR. Nothing downstream catches a wrong description: it never turns a check red, and each later run re-anchors one increment further out.
