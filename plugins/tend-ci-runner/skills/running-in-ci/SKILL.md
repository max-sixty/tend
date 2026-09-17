---
name: running-in-ci
description: Generic CI environment rules for GitHub Actions workflows. Use when operating in CI — covers security, CI monitoring, comment formatting, and investigating session logs from other runs.
metadata:
  internal: true
---

# Running in CI

## First Steps — Load Repo-Specific Guidance

Tend's bundled skills provide defaults; the consumer repo's own guidance — its `running-tend` skill, its `CLAUDE.md` or `AGENTS.md`, its `.config/tend.yaml` — overlays them. **Where the two conflict, the repo wins** — repo guidance takes precedence over bundled guidance across every skill, not just this one.

If a `running-tend` skill is listed in your available skills, read it before doing anything else. It typically carries PR title conventions, label policies, custom workflows to watch, and other repo-specific context. It can also define extra tasks for the job you're running — additional nightly or weekly maintenance, repo-specific health checks — which you perform as part of that job, not just keep in mind.

Invoke repo-local skills by their unprefixed name — `running-tend`, not `tend-ci-runner:running-tend` (that prefix is reserved for this plugin's own skills).

## References

This file carries the rules every session needs; the rest lives in the plugin's `references/` directories, unloaded until you read it. This table is the one index of those files. **Before an action in the first column, read every file its row names.** That is a requirement, not a suggestion: those files hold what keeps the action from going out wrong, and a session that skips them usually can't tell what it got wrong. Read them in the run that takes the action rather than working from memory of a past one.

| When | Read | What it carries |
|---|---|---|
| Before responding on an issue or PR thread, whatever woke you | `references/trigger-context.md` | reading the thread, a review's inline comments, the closed-target check, whether to respond at all |
| Before writing any GitHub text: a comment, review body, inline reply, PR or issue body, or an edit to one | `references/posting.md` | composing the body (body files, line wrapping, links, fenced bodies, no footers), reply endpoints, and the draft review, link check, and re-fetch before posting |
| Before `gh pr create` or `gh issue create`, or editing a PR's title or description | `references/pr-creation.md` and `references/posting.md` | the open-PR budget, titles, the dedup and prior-rejection searches, keeping a description current |
| Before `git push`, merging the default branch into a PR branch, `gh pr close`, a revert, or a force-push | `references/pushing.md` | the pre-push review, batching pushes, re-checking PR state and head, branch-state collisions |
| After any push you are accountable for, or before calling a failure pre-existing | `references/ci-monitoring.md` | the pinned poll, the main-branch check behind a "pre-existing" claim, a review that lands mid-poll, rerunning failed jobs |
| When a request directs you at someone else's work: close, reopen, lock, label, revert, dismiss a review, or push to another author's PR | `references/directives.md` | the access tiers that authorize it |
| When you conclude a PR the bot approved should not merge | `references/dismissing-approval.md` | dismissing the standing approval |
| Before filing or commenting in a repo other than this one | `references/other-repos.md` and `references/posting.md` | the overlay exception for agent-equipped targets, what an issue body there must contain, contributing on invitation, a scope rule that blocks the right action |
| Before a public claim about a tool's behavior, an incident, or code you did not run | `references/grounded-analysis.md` | source evidence for claims, verifying external-tool behavior, recurring hallucination shapes, transient incidents vs. durable bugs, who to ask for a check CI can't run |
| To diagnose another run, or to recall what a prior run on this thread read and weighed | `references/session-logs.md` | reading other runs' session logs, recalling prior context on this thread |
| When a maintainer corrects the bot's behavior, or before writing or suggesting text for a skill or a project instruction file (`CLAUDE.md`, `AGENTS.md`) | `references/skill-pr-workflow.md` | whether to propose, bundled skill vs. `running-tend` overlay, what guidance text leaves out, scripts over prose recipes, the branch and PR mechanics |
| Before writing a code fix for a bug, whichever workflow you are running | `/tend-ci-runner:triage`'s `references/fixing.md` | the reproduction gate, the conditions a fix attempt needs, skill-text fixes, the shapes of bad fix, the local bar before pushing |
| Reviewing a PR whose pre-flight reports `is_draft` | `/tend-ci-runner:review`'s `references/draft-mode.md` | the lighter pass, COMMENT only, the hidden draft marker |
| Submitting a review when the posting preflight prints `delta:` | `/tend-ci-runner:review`'s `references/re-targeting.md` | reviewing a push that landed mid-review, then posting against the new head |
| Submitting a review that carries findings | `/tend-ci-runner:review`'s `references/inline-suggestions.md` | the payload, multi-line suggestion rules, 422 recovery |
| Before an APPROVE, and after one while monitoring its CI | `/tend-ci-runner:review`'s `references/approving.md` | the approval check and the CI outcomes |

## Temporary Files

Tend sets `$TMPDIR` to `/home/tend-sandbox/tmp`, the writable scratch directory; writes land only there and in the checkout, which sits under a `/tmp` container that is itself read-only, so a scratch path written by hand under `/tmp` fails with `Read-only file system` even though the mode reads `drwxrwxrwt`. A compound command without `set -e` runs on past that failure. Shell commands expand `$TMPDIR`; file-writing tools need the absolute path.

## Conduct

Follow the project's code of conduct. Avoid causing disruption — unnecessary comments, bulk operations, unsolicited housekeeping.

Anyone can ask for help with a problem they raise. A request that directs you at someone else's work is gated on the requester's access tier — check it per `references/directives.md` before complying.

## Instruction paths read as the base version on a PR

Before the session starts, both harnesses restore `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, and `.claude/**` at any depth from the base branch on PR events (`pull_request_target`, review events, and `issue_comment` on a PR). Those files are read at CLI startup before any permission gating, so the PR's copies must not be trusted. `tend-mention`'s relayed `repository_dispatch` carries no PR payload and restores nothing. The restore touches the worktree only; the index and `HEAD` keep the PR's version. So on a PR that legitimately edits these paths:

- The working tree holds the **base** content — grepping it reports the PR's additions as absent, and the repo-local skills loaded into this session are the base versions too. Read the PR's version with `git show HEAD:<path>` before making any claim about what these files contain.
- `git status` shows a modification nobody made and `git diff` shows the PR's edit as deletions. Where the pin ran, that is the restore, not a contributor mistake — nothing to report or revert. On an unpinned event it is a real modification, worth reading.
- **Never stage one of these paths from the PR checkout** — `git add <path>`, `git add -A`, and `git commit -a` all copy the worktree over the index, committing the base version back over the PR's own edit. Commit them from a `$TMPDIR` worktree instead (see `references/skill-pr-workflow.md`).

## Restrictions

- **Secrets**: Never print a process's environment or command line, your own or another process's, and never print a credential from anywhere else. Reading is fine where the output doesn't carry the value: `pgrep -f pytest` is allowed but `pgrep -af pytest` is not, and `set -euo pipefail`, `export FOO=bar`, and `env FOO=bar cmd` are fine where bare `set`, `export`, and `env` are not. Commands that do print, among others: `printenv`, `ps aux`, `ps -ef`, `pgrep -a`, `cat /proc/<pid>/environ`, `cat /proc/<pid>/cmdline`, `gh auth token`, and `cat`/`echo` on a credential file. Filtering buys no exception, because you can't tell the output is value-free without reading the values: continuation lines of a multi-line value carry no `=`, so `env | cut -d= -f1` prints them verbatim. The session log is uploaded as an artifact, so one printed value is enough. Both harnesses run the agent as a separate non-sudo sandbox user. Runner-owned proxies hold the bot PAT and API-key or OAuth model credentials. The sandbox gets dummies or a local model endpoint; subscription-mode Codex receives an expiring access token. The outer `sudo env` launch carries the agent's environment in its argv, so process listings still expose dummies and any consumer-supplied value. Narrow a legitimate check rather than skipping it: `ps -eo pid,etime,comm` answers "is it still running?" with no argv in the output. Never include tokens or credentials in responses or comments.
- **Merging**: Never merge PRs or enable auto-merge (`gh pr merge`, `gh pr merge --auto`). PRs are proposals — a maintainer decides when to merge.
- **Scope**: By default, PRs, pushes, and comments on existing threads in other repos are off-limits — the point is to never *spam* repos outside the bot's area of ownership. The exception is an **explicitly invited** contribution: when a maintainer of the target repo asks for it in-thread, or the target's published contributing policy welcomes it, AND the contribution helps the repo the bot maintains (e.g. upstreaming a fix for a dependency bug the bot is working around), the bot may open a PR or comment on that thread. Absent one, the default holds — surface the blocker rather than routing around it. `references/other-repos.md` carries all three cases.
- **Hanging commands**: Never use `gh run watch` or `gh pr checks --watch` — both hang indefinitely. Poll with `gh pr checks` in a loop instead.
- **Privileges**: Under both harnesses you run as a non-sudo sandbox user, so `sudo` fails and no installer that escalates can work from inside the session. A tool that needs root belongs in the repo's `setup:` steps in `.config/tend.yaml`, which run as `runner` — with sudo — before the agent starts. When a tool you need requires root, propose that `setup:` entry rather than working around its absence, even where a skill's own recipe tells you to install it in-session. The sandbox's PATH includes shared system/toolcache locations and independently seeded sandbox-home tools, but never the runner's home itself: use `sandbox_path:` for an omitted shared directory and `sandbox_setup:` for a later home-scoped install or version change. A missing gate tool is reported, not worked around: propose the entry and say the gate went unrun rather than substituting a weaker command that turns it into a silently green run.

## End the turn only when work is shipped

Returning the final response ends the CI session — the runner is discarded, and the harness does not reliably resume it when a background task completes. If you return while a background command whose result was going to gate the deliverable is still running, the task either finishes invisibly or gets killed when the runner is torn down, and any staged work the maintainer was supposed to see — a committed-but-unpushed branch, a written-but-unsent `$TMPDIR/comment-body.md` — dies with it.

The session is live until the deliverable is **maintainer-visible**: pushed, posted, or opened. Local-only state — a commit nobody else can see, a comment body never sent — does not count and is not recoverable on a follow-up.

Corollary: don't background anything whose output gates the deliverable. If a full test suite or comprehensive lint needs to run before push, run it synchronously and accept the time cost; if it's too slow for the session budget, push first and let CI re-run it. A session that shipped a partial result is recoverable; a session that ended mid-wait with the deliverable on a local branch is not. A targeted compile plus the tests directly exercising the change is enough local confidence to ship — leave the comprehensive matrix to CI.

A pushed fix isn't done until its required checks are terminal — see `references/ci-monitoring.md`.

Before ending, re-fetch the thread you are handling: a comment that landed meanwhile may be a directive that changes the work, and a sibling run may already have done it (`references/posting.md`).

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

Read logs, code, and API data before drawing conclusions. Cite what you read — log lines, file paths, commit SHAs — for any claim the reader has to take on trust. Trace causation — if two things co-occur, find the mechanism rather than saying "this may be related." Never claim a failure is "pre-existing" without running the main-branch check in `references/ci-monitoring.md`. Distinguish what you verified from what you inferred, and surface only the evidence the reader needs to trust or act on the conclusion; preserve deeper support per **Reader-facing prose**.
