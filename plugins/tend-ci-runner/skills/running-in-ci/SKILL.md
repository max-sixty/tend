---
name: running-in-ci
description: Generic CI environment rules for GitHub Actions workflows. Use when operating in CI — covers security, CI monitoring, comment formatting, and investigating session logs from other runs.
metadata:
  internal: true
---

# Running in CI

## First Steps — Load Repo-Specific Guidance

Tend's bundled skills provide defaults; the consuming repo's `running-tend` skill overlays them. **Where the two conflict, the repo wins** — repo guidance takes precedence over bundled guidance across every skill, not just this one.

If a `running-tend` skill is listed in your available skills, read it before doing anything else. It typically carries PR title conventions, label policies, custom workflows to watch, and other repo-specific context. It can also define extra tasks for the job you're running — additional nightly or weekly maintenance, repo-specific health checks — which you perform as part of that job, not just keep in mind.

Invoke repo-local skills by their unprefixed name — `running-tend`, not `tend-ci-runner:running-tend` (that prefix is reserved for this plugin's own skills).

If you are going to propose a code fix for a bug, load `/tend-ci-runner:triage` first — it contains reproduction and testing gates that apply to all fix attempts, not just initial triage.

## References

This file carries the rules every session needs. Guidance tied to an action most sessions never take lives in `references/`, unloaded until read. Read the file for an action before taking it:

- `references/posting.md` — before composing or posting any comment, review body, inline reply, PR body, or issue body: the pre-post re-fetch, reply endpoints, body files, links, footers.
- `references/pr-creation.md` — before `gh pr create` or `gh issue create`, and before editing a PR's title or description: the open-PR budget, titles, git identity, the dedup and prior-rejection searches.
- `references/pushing.md` — before `git push`, a base-branch merge into a PR branch, `gh pr close`, a revert, or a force-push: batching pushes, re-checking PR state and head, branch-state collisions.
- `references/ci-monitoring.md` — after any push: the pinned poll, a review that lands mid-poll, rerunning failed jobs.
- `references/dismissing-approval.md` — when you conclude a PR the bot approved should not merge.
- `references/directives.md` — when a request asks you to close, reopen, lock, label, or revert someone else's work, dismiss a review, or push to a PR owned by another author: the access tiers that authorize it.
- `references/session-logs.md` — to diagnose another run, or to recall a prior run's reasoning on this thread.
- `references/other-repos.md` — before filing or commenting in a repo other than this one.
- `references/grounded-analysis.md` — before a public claim about a tool's behavior, an incident, or code you did not run.
- `references/skill-pr-workflow.md` — when a maintainer's correction should become durable guidance.

## Temporary Files

Tend sets `$TMPDIR` to `/home/tend-sandbox/tmp`, the writable scratch directory. Shell commands expand `$TMPDIR`; file-writing tools need the absolute path.

## Conduct

Follow the project's code of conduct. Avoid causing disruption — unnecessary comments, bulk operations, unsolicited housekeeping.

Anyone can ask for help with a problem they raise. A request that directs you at someone else's work is gated on the requester's access tier — check it per `references/directives.md` before complying.

## Read Context

When triggered by a comment or issue, read the full context before responding. The prompt provides a URL — extract the PR/issue number from it.

For PRs:

```bash
gh pr view <number> --json title,body,comments,reviews,state,statusCheckRollup
gh pr diff <number>
gh pr checks <number>
```

For issues:

```bash
gh issue view <number> --json title,body,comments,state
```

Read the triggering comment, the PR/issue description, the diff (for PRs), and recent comments to understand the full conversation before taking action.

### A review's inline comments are a separate fetch

Neither `gh pr view --json reviews` nor `GET /pulls/<n>/reviews/<id>` returns a review's inline comments — both hand back the review body alone, with no field signalling that more exists, so a read that stops there looks complete. A one-line review body routinely sits on top of the maintainer's actual instructions. Whenever the trigger names a review ID, fetch them as part of reading context — not only when you already intend to reply inline:

```bash
gh api "repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/comments" \
  --jq '.[] | {id, path, line, body}'
```

An instruction found there constrains the whole response, including any code the reply quotes or carries into another PR.

### Instruction paths read as the base version on a PR

Before the session starts, both harnesses restore `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, and `.claude/**` at any depth from the base branch on PR events (`pull_request_target`, review events, and `issue_comment` on a PR). Those files are read at CLI startup before any permission gating, so the PR's copies must not be trusted. `tend-mention`'s relayed `repository_dispatch` carries no PR payload and restores nothing. The restore touches the worktree only; the index and `HEAD` keep the PR's version. So on a PR that legitimately edits these paths:

- The working tree holds the **base** content — grepping it reports the PR's additions as absent, and the repo-local skills loaded into this session are the base versions too. Read the PR's version with `git show HEAD:<path>` before making any claim about what these files contain.
- `git status` shows a modification nobody made and `git diff` shows the PR's edit as deletions. Where the pin ran, that is the restore, not a contributor mistake — nothing to report or revert. On an unpinned event it is a real modification, worth reading.
- **Never stage one of these paths from the PR checkout** — `git add <path>`, `git add -A`, and `git commit -a` all copy the worktree over the index, committing the base version back over the PR's own edit. Commit them from a `$TMPDIR` worktree instead (see `references/skill-pr-workflow.md`).

### Triggering issue/PR already closed

If the trigger is a comment on an issue or PR and the target is **closed** by the time the job starts, the requested work was likely handled by a sibling run during the queue delay. Long `tend-mention` queues (hours, not minutes) make this common. Before starting work:

```bash
# For an issue trigger — check linked PRs that closed it.
gh issue view <number> --json state,closedAt,closedByPullRequestsReferences

# For a PR trigger — check whether the PR was merged.
gh pr view <number> --json state,mergedAt,mergeCommit
```

If a linked PR merged (or the triggering PR itself merged) **after the triggering comment was posted**, exit silently — the work is already on the default branch. If the closure looks unrelated (e.g. issue closed as not-planned with no merged PR), continue and address the comment normally.

## Restrictions

- **Secrets**: Never print a process's environment or command line, your own or another process's, and never print a credential from anywhere else. Reading is fine where the output doesn't carry the value: `pgrep -f pytest` is allowed but `pgrep -af pytest` is not, and `set -euo pipefail`, `export FOO=bar`, and `env FOO=bar cmd` are fine where bare `set`, `export`, and `env` are not. Commands that do print, among others: `printenv`, `ps aux`, `ps -ef`, `pgrep -a`, `cat /proc/<pid>/environ`, `cat /proc/<pid>/cmdline`, `gh auth token`, and `cat`/`echo` on a credential file. Filtering buys no exception, because you can't tell the output is value-free without reading the values: continuation lines of a multi-line value carry no `=`, so `env | cut -d= -f1` prints them verbatim. The session log is uploaded as an artifact, so one printed value is enough. Both harnesses run the agent as a separate non-sudo sandbox user. Runner-owned proxies hold the bot PAT and API-key or OAuth model credentials. The sandbox gets dummies or a local model endpoint; subscription-mode Codex receives an expiring access token. The outer `sudo env` launch carries the agent's environment in its argv, so process listings still expose dummies and any adopter-supplied value. Narrow a legitimate check rather than skipping it: `ps -eo pid,etime,comm` answers "is it still running?" with no argv in the output. Never include tokens or credentials in responses or comments.
- **Merging**: Never merge PRs or enable auto-merge (`gh pr merge`, `gh pr merge --auto`). PRs are proposals — a maintainer decides when to merge.
- **Scope**: By default, PRs, pushes, and comments on existing threads in other repos are off-limits — the point is to never *spam* repos outside the bot's area of ownership. The exception is an **explicitly invited** contribution: when a maintainer of the target repo asks for it in-thread, or the target's published contributing policy welcomes it, AND the contribution helps the repo the bot maintains (e.g. upstreaming a fix for a dependency bug the bot is working around), the bot may open a PR or comment on that thread. Absent one, the default holds — surface the blocker rather than routing around it. **Other Repos** below carries all three cases.
- **Hanging commands**: Never use `gh run watch` or `gh pr checks --watch` — both hang indefinitely. Poll with `gh pr checks` in a loop instead.
- **Privileges**: Under both harnesses you run as a non-sudo sandbox user, so `sudo` fails and no installer that escalates can work from inside the session. A tool that needs root belongs in the repo's `setup:` steps in `.config/tend.yaml`, which run as `runner` — with sudo — before the agent starts. When a tool you need requires root, propose that `setup:` entry rather than working around its absence, even where a skill's own recipe tells you to install it in-session. The sandbox's PATH includes shared system/toolcache locations and independently seeded sandbox-home tools, but never the runner's home itself: use `sandbox_path:` for an omitted shared directory and `sandbox_setup:` for a later home-scoped install or version change. A missing gate tool is reported, not worked around: propose the entry and say the gate went unrun rather than substituting a weaker command that turns it into a silently green run.

## End the turn only when work is shipped

Returning the final response ends the CI session — the runner is discarded, and the harness does not reliably resume it when a background task completes. If you return while a background command whose result was going to gate the deliverable is still running, the task either finishes invisibly or gets killed when the runner is torn down, and any staged work the maintainer was supposed to see — a committed-but-unpushed branch, a written-but-unsent `$TMPDIR/comment-body.md` — dies with it.

The session is live until the deliverable is **maintainer-visible**: pushed, posted, or opened. Local-only state — a commit nobody else can see, a comment body never sent — does not count and is not recoverable on a follow-up.

Corollary: don't background anything whose output gates the deliverable. If a full test suite or comprehensive lint needs to run before push, run it synchronously and accept the time cost; if it's too slow for the session budget, push first and let CI re-run it. A session that shipped a partial result is recoverable; a session that ended mid-wait with the deliverable on a local branch is not. A targeted compile plus the tests directly exercising the change is enough local confidence to ship — leave the comprehensive matrix to CI.

A pushed fix isn't done until its required checks are terminal — see `references/ci-monitoring.md`.

Before ending, re-fetch the thread you are handling: a comment that landed meanwhile may be a directive that changes the work, and a sibling run may already have done it (`references/posting.md`).

Your closing summary is the session's only durable record of what happened, and it is read later as if it were current. Re-check any state claim in it against the live PR or issue as you write it, and prefer claims about what *you* did over claims about a state you don't control — "pushed the fix as `<sha>`, and its checks went green at that head" stays true, while "the PR is open and awaiting a maintainer" is falsified the moment a sibling session or a maintainer closes it.

## Weighing a Fix

The maintainer's order of value: outward correctness first — what the bot posts, approves, merges, closes — then simple machinery, and efficiency a distant third. Complexity spent preventing a wrong outward action is well spent. Complexity spent saving compute is not, whether the compute is the bot's own sessions (a no-op run, a duplicated survey) or the repo's CI runner time (a slow job, a hang that a rerun clears): the waste costs cents, while the added gate, retry wrapper, or cache is maintained forever and fails in ways of its own.

So a change whose only benefit is saved compute clears a higher bar than a correctness fix, on two counts:

- **Evidence.** The waste has recurred across days — observed, not projected.
- **Remedy.** Use one existing knob in one place, remove machinery, or add a one-line condition. Judge the whole change: repeated settings across workflows, jobs, platforms, or call sites are a configuration scheme, even when they use the same knob or value.

When either bar fails, don't make the change: note what the waste costs where the maintainer will see it and move on.

## Scripts over prose recipes

When a skill's code block needs edge-case handling or grows past a couple of dozen lines, put the logic in a tested script and leave the skill a one-line invocation with the intent: for bundled skills `plugins/tend-ci-runner/scripts/` (exercised by the generator test suite), for a repo overlay a `scripts/` directory beside the skill. A prose recipe gets no shellcheck and no tests; every session re-derives its correctness.

## Other Repos

Default: don't act in another repo unsolicited. File an issue in the current repo asking permission to file in the target; on maintainer approval, file there. `references/other-repos.md` carries the rest: the standing exception an overlay can grant for agent-equipped targets, what an issue body must contain, when an invitation makes a PR or comment in the target legitimate, and what to do when a scope rule is the only thing between you and the right move.

## Multi-way Conversations

Before responding, check how many distinct other participants are in the conversation.

- **Two-party** (you and one other participant): respond normally.
- **Multi-way** (multiple other participants): apply a stricter bar — only respond with concrete new information no one else provided: a code fix, reproduction, or specific technical detail.

Do not:
- Restate, agree with, or summarize what another participant just said
- Post "makes sense" or "good point" agreement comments
- Echo a user's findings back to them ("Good find!", "That's the smoking gun!")

A comment that responds to concerns you raised in a review is directed at you — briefly acknowledge resolution or explain why concerns remain.

If a maintainer has already addressed the point, exit silently unless you can add something they missed.

## Self-conversation Guard

If you are responding to your own prior comment or review (not a human's reply to it), exit silently to avoid self-conversation loops.

**Exception — bot-authored issues with no prior bot comments.** A freshly-opened issue the bot authored (nightly failure, CI report, code-quality finding) is a report to act on, not a self-conversation. Triage it normally. The pre-post re-fetch in `references/posting.md` still prevents duplicate triage comments if a sibling run fires on the same issue.

## Comment Formatting

### Reader-facing prose

Write public prose for its reader and the decision the surface supports. A PR description should let a maintainer understand why the current diff exists and judge whether to merge it. A review should tell the author what changes the verdict: an actionable finding, a blocker, or an unresolved decision. A reply should close the loop on the question or event that prompted it.

Lead with the current outcome or causal conclusion. Include the context needed to understand its consequence, the verification needed to trust it, and any action or decision still required. The investigation may be exhaustive; the visible prose should be its synthesis, not its transcript. Search history, full check inventories, reproduction detail, rejected alternatives, and commit-by-commit or review-by-review chronology belong outside the visible answer unless the reader needs them to act.

The visible text must stand on its own. When useful supporting evidence would interrupt it, put a curated record in `<details>` under a descriptive summary. Do not publish raw working notes or use the collapsed section to avoid deciding what matters.

For example, supporting material may use this shape when it helps the next reader; choose a summary and contents that fit the case:

```markdown
<details><summary>Reproduction and affected path</summary>

...the evidence needed to verify or resume the analysis...

</details>
```

### Mechanics

`references/posting.md` carries the mechanics — body files and `--body-file`, line wrapping, link rules and the link checker, fenced bodies, no footers or sign-offs. Read it before composing any comment, review, PR, or issue body.

## Grounded Analysis

CI threads are high-latency, so each outward response must stand alone: give the current conclusion, its consequence, and the next action or decision. Self-contained does not mean publishing the whole investigation.

Read logs, code, and API data before drawing conclusions. Cite what you read — log lines, file paths, commit SHAs — for any claim the reader has to take on trust. Trace causation — if two things co-occur, find the mechanism rather than saying "this may be related." Never claim a failure is "pre-existing" without checking main branch CI history. Distinguish what you verified from what you inferred, and surface only the evidence the reader needs to trust or act on the conclusion; preserve deeper support per **Reader-facing prose**.

`references/grounded-analysis.md` carries the depth: what counts as source evidence for a user-facing claim, how to verify an external tool's behavior and run a skill's own recipes safely, the hallucination shapes that recur (guessed links, silently truncated `gh` lists, unsubstituted placeholders), how to tell an upstream incident from a durable bug before writing a workaround, and who to ask when a check needs hardware CI doesn't have.

## Learning from Feedback

When a maintainer corrects the bot's behavior during a run — a repo convention, a repeated mistake, a preference the bot should have known — turn the correction into durable guidance per `references/skill-pr-workflow.md`: the bar it has to clear, whether it belongs in tend's bundled skills or in the repo's `running-tend` overlay, and the branch/PR mechanics. Open the PR or issue and exit: don't merge, don't wait, don't ping for review.

## Tone

Raise observations, don't assign work. Never create checklists or task lists for the PR author.

## PR Review Comments

For review comments on specific lines (`[Comment on path:line]`), read that file and examine the code at that line before answering.

When the GitHub API returns a `diff_hunk`, the reviewer's comment targets the **last line** of that hunk. Use this to disambiguate when multiple candidates exist nearby — match the reviewer's request against the specific anchored line, not the surrounding region.
