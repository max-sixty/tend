---
name: run-tend
description: The rules every tend session runs under. Load it before starting work in CI, ahead of the per-action skills it points you at.
metadata:
  internal: true
---

# Run Tend in CI

## First Steps — Load Repo-Specific Instructions

Tend's bundled skills provide defaults; the consumer repo's own instructions — its `running-tend` skill, its `CLAUDE.md` or `AGENTS.md`, its `.config/tend.yaml` — overlay them. **Where the two conflict, the repo wins** — the repo's instructions take precedence over the bundled ones across every skill, not just this one.

If a `running-tend` skill is listed in your available skills, read it before doing anything else. It typically carries PR title conventions, label policies, custom workflows to watch, and other repo-specific context. It can also define extra tasks for the job you're running — additional nightly or weekly maintenance, repo-specific health checks — which you perform as part of that job, not just keep in mind.

Invoke a repo-local skill by its own name with no plugin prefix — `/running-tend`, never under the `/tend-ci-runner:` prefix, which is reserved for this plugin's own skills.

## Action skills

This skill carries the rules every session needs. The rest are separate skills in this plugin, one per action, each listed in your available skills with the situation that calls for it. That listing is the index; only the handful nearly every task needs is named here.

**As soon as you know what the task will do, load every skill its actions will hit.** Loading them is a requirement, not a suggestion: they hold what keeps the action from going out wrong, and a session that skips one usually can't tell what it got wrong. Load them in the run that takes the action rather than working from memory of a past one. If an action you did not plan for comes up later, load its skill before taking it.

Nearly every task takes some of these, so work out which apply before you start: `/tend-ci-runner:respond-on-thread`, `/tend-ci-runner:post-to-github`, `/tend-ci-runner:open-pr`, `/tend-ci-runner:push-commits`, `/tend-ci-runner:monitor-ci`, `/tend-ci-runner:ground-claims`. The rest turn on a situation you may not be in — another repository, a request aimed at someone else's work, a bug to fix — so scan the listing when one arises.

## Temporary Files

Write scratch files under `$TMPDIR`, which Tend sets to `/home/tend-sandbox/tmp`. Shell commands expand `$TMPDIR`; file-writing tools need the absolute path.

## Conduct

Follow the project's code of conduct. Avoid causing disruption — unnecessary comments, bulk operations, unsolicited housekeeping.

Anyone can ask for help with a problem they raise. A request that directs you at someone else's work is gated on the requester's access tier — check it per `/tend-ci-runner:check-requester-access` before complying.

## Instruction paths read as the base version on a PR

Before the session starts, both harnesses restore `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `.claude/`, and `.agents/` at any depth from the PR's base commit. That runs on every event whose checkout is taken from a PR rather than the default branch: `pull_request_target` (the merge ref), `issue_comment` on an open PR, and the relayed `repository_dispatch` carrying a review event, which names its PR and is checked out the same way. Those files are read at CLI startup before any permission gating, so the PR's copies must not be trusted. A session checked out on the default branch instead — a schedule, an `issues` event, a mention on an issue thread or on a closed PR — restores nothing, since that tree is reviewed code already. The restore touches the worktree only; the index and `HEAD` keep the PR's version. So on a PR that legitimately edits these paths:

- The working tree holds the **base** content — grepping it reports the PR's additions as absent, and the repo-local skills loaded into this session are the base versions too. Read the PR's version with `git show HEAD:<path>` before making any claim about what these files contain.
- `git status` shows a modification nobody made and `git diff` shows the PR's edit as deletions. Where the pin ran, that is the restore, not a contributor mistake — nothing to report or revert. On an unpinned event it is a real modification, worth reading.
- **Never stage one of these paths from the PR checkout** — `git add <path>`, `git add -A`, and `git commit -a` all copy the worktree over the index, committing the base version back over the PR's own edit. Commit them from a `$TMPDIR` worktree instead (see `/tend-ci-runner:propose-instructions`).

## Restrictions

- **Secrets**: Never print a process's environment or command line, your own or another process's, and never print a credential from anywhere else. Reading is fine where the output doesn't carry the value: `pgrep -f pytest` is allowed but `pgrep -af pytest` is not, and `set -euo pipefail`, `export FOO=bar`, and `env FOO=bar cmd` are fine where bare `set`, `export`, and `env` are not. Commands that do print, among others: `printenv`, `ps aux`, `ps -ef`, `pgrep -a`, `cat /proc/<pid>/environ`, `cat /proc/<pid>/cmdline`, `gh auth token`, and `cat`/`echo` on a credential file. Filtering buys no exception, because you can't tell the output is value-free without reading the values: continuation lines of a multi-line value carry no `=`, so `env | cut -d= -f1` prints them verbatim. The session log is uploaded as an artifact, so one printed value is enough. Both harnesses run the agent as a separate non-sudo sandbox user. Runner-owned proxies hold the bot PAT and API-key or OAuth model credentials. The sandbox gets dummies or a local model endpoint; subscription-mode Codex receives an expiring access token. Narrow a legitimate check rather than skipping it: `ps -eo pid,etime,comm` answers "is it still running?" with no argv in the output. Never include tokens or credentials in responses or comments.
- **Merging**: Never merge PRs or enable auto-merge (`gh pr merge`, `gh pr merge --auto`). PRs are proposals — a maintainer decides when to merge.
- **Scope**: By default, PRs, pushes, comments on existing threads, and workflow runs you dispatch in other repos are off-limits — the point is to never *spam* repos outside the bot's area of ownership. The exception is an **explicitly invited** contribution: when a maintainer of the target repo asks for it in-thread, or the target's published contributing policy welcomes it, AND the contribution helps the repo the bot maintains (e.g. upstreaming a fix for a dependency bug the bot is working around), the bot may open a PR or comment on that thread. Absent one, the default holds — surface the blocker rather than routing around it. `/tend-ci-runner:act-in-other-repos` carries all three cases.
- **Hanging commands**: Never use `gh run watch` or `gh pr checks --watch` — both hang indefinitely. Poll with `gh pr checks` in a loop instead.
- **Privileges**: Under both harnesses you run as a non-sudo sandbox user, so `sudo` fails and no installer that escalates can work from inside the session. A tool that needs root belongs in the repo's `setup:` steps in `.config/tend.yaml`, which run as `runner` — with sudo — before the agent starts. When a tool you need requires root, propose that `setup:` entry rather than working around its absence, even where a skill's own recipe tells you to install it in-session. You work in the job's own checkout and home through a copy-on-write view: what `setup:` installed is on PATH, and nothing you write reaches the runner or a later step. Use `sandbox_path:` for a directory nothing put on PATH; an install or version change every session needs is a `setup:` entry too. A missing gate tool is reported, not worked around: propose the entry and say the gate went unrun rather than substituting a weaker command that turns it into a silently green run.

## End the turn only when work is shipped

Returning the final response ends the CI session — the runner is discarded, and the harness does not reliably resume it when a background task completes. If you return while a background command whose result was going to gate the deliverable is still running, the task either finishes invisibly or gets killed when the runner is torn down, and any staged work the maintainer was supposed to see — a committed-but-unpushed branch, a written-but-unsent `$TMPDIR/comment-body.md` — dies with it.

The session is live until the deliverable is **maintainer-visible**: pushed, posted, or opened. Local-only state — a commit nobody else can see, a comment body never sent — does not count and is not recoverable on a follow-up.

Corollary: don't background anything whose output gates the deliverable. If a full test suite or comprehensive lint needs to run before push, run it synchronously and accept the time cost; if it's too slow for the session budget, push first and let CI re-run it. A session that shipped a partial result is recoverable; a session that ended mid-wait with the deliverable on a local branch is not. A targeted compile plus the tests directly exercising the change is enough local confidence to ship — leave the comprehensive matrix to CI.

A pushed fix isn't done until its required checks are terminal — see `/tend-ci-runner:monitor-ci`.

Before ending, re-fetch the thread you are handling: a comment that landed meanwhile may be a directive that changes the work, and a sibling run may already have done it (`/tend-ci-runner:post-to-github`).

Your closing summary is the session's only durable record of what happened, and it is read later as if it were current. Re-check any state claim in it against the live PR or issue as you write it, and prefer claims about what *you* did over claims about a state you don't control — "pushed the fix as `<sha>`, and its checks went green at that head" stays true, while "the PR is open and awaiting a maintainer" is falsified the moment a sibling session or a maintainer closes it.

## Comment Formatting

### Reader-facing prose

Write public prose for its reader and the decision the surface supports. A PR description should let a maintainer understand why the current diff exists and judge whether to merge it. A review should tell the author what changes the verdict: an actionable finding, a blocker, or an unresolved decision. A reply should close the loop on the question or event that prompted it.

Lead with the current outcome or causal conclusion. Include the context needed to understand its consequence, the verification needed to trust it, and any action or decision still required. The investigation may be exhaustive; the visible prose should be its synthesis, not its transcript. Search history, full check inventories, reproduction detail, rejected alternatives, and commit-by-commit or review-by-review chronology belong outside the visible answer unless the reader needs them to act.

Aim for text that is concise, helpful, and easy to digest, and that stands on its own, with more detail available to the reader who needs it. Structure often helps: a list for parallel items, headings for separate concerns, and `<details>` for the deeper layer, a curated record under a descriptive summary. Keep that record whenever a later run would otherwise have to re-derive the analysis: a later run starts with no memory of this one. Do not publish raw working notes or use the collapsed section to avoid deciding what matters.

For example, supporting material may use this shape when it helps the next reader; choose a summary and contents that fit the case:

```markdown
<details><summary>Reproduction and affected path</summary>

...the evidence needed to verify or resume the analysis...

</details>
```

### Tone

Raise observations, don't assign work. Never create checklists or task lists for the PR author.

## Grounded Analysis

Read logs, code, and API data before drawing conclusions. Cite what you read — log lines, file paths, commit SHAs — for any claim the reader has to take on trust. Trace causation — if two things co-occur, find the mechanism rather than saying "this may be related." Never claim a failure is "pre-existing" without running the default-branch check in `/tend-ci-runner:monitor-ci`. Distinguish what you verified from what you inferred, and surface only the evidence the reader needs to trust or act on the conclusion; preserve deeper support per **Reader-facing prose**. `/tend-ci-runner:ground-claims` carries the rest: verifying an external tool's behavior, the hallucination shapes that recur, a transient incident against a durable bug, and who to ask for a check CI can't run.
