---
name: merge-pr
description: Complete the landing decision for a reviewed PR after CI verification, under the repository's merge mode.
metadata:
  internal: true
---

# Merge a PR

Use the review and CI evidence for the exact head the calling workflow
completed. If matching CI evidence is missing, obtain it per
`/tend-ci-runner:monitor-ci` first. Monitoring alone does not make a head reviewed.

Under `restricted`, leave the verified proposal for a maintainer. Under `yolo`,
complete the gates below and attempt the ordinary pinned merge.

## Reviewed head

`PINNED_SHA` must be the current head with a standing bot approval
(`fresh_approval_sha == PINNED_SHA` from `tend runtime bot-review-state state <number>`),
or, on the bot's own PR, a review of that head by this session warranting
approval, including a trivial-increment close-out. GitHub forbids self-approval.
A landing session tend-land started stands on the review that recorded the
head: a `tend/land/<number>` status the bot created on `PINNED_SHA`
(`tend runtime land-pr`), whose description names the base the review judged and the
checks it skipped. Read it (`gh api repos/{owner}/{repo}/commits/<sha>/statuses`),
leave the PR open if the base has changed since, and apply the CI policy with
those skips, reading CI once without waiting. If checks are running again (a
rerun since tend-land saw green), end: their completion starts another
landing. Everything below still applies: the record settles nothing that
happened after it.
A caller with neither reviews the head per `/tend-ci-runner:review` before
landing, or leaves that review with an identified queued reviewer.

## CI landing policy

Apply the repository overlay's CI landing policy to the pinned commit. The
default requires exit 0. Where the overlay permits landing with terminal
failures, verify and record the evidence its conditions require; the poll's red
verdict alone does not override that policy. Return failures the policy does
not cover to the calling workflow for repair or an explicit blocker. Pending
checks and unverified results do not establish a terminal-failure exception.

## Readiness and ownership

Re-read the PR and its inline review comments. A draft, an unretracted human
merge hold, an unresolved actionable finding, or an independent owning run
leaves the verified PR open.

### Subject-run ownership

Check for another dedicated owner:

```bash
tend runtime active-subject-runs \
  "https://api.github.com/repos/$GITHUB_REPOSITORY/pulls/<number>"
```

An active subject-run listing identifies candidates, not ownership. Check the
workflow's actual concurrency to establish whether another run can progress
independently. A run waiting behind this session is a successor: hand work to
it only when it has remaining work this session cannot complete, such as
reviewing a newly pushed head. Otherwise finish here.

The CI poll omits Tend's review check: a pushed head still awaiting review stays
open for that review, even when its other checks are green.

## Attempt the pinned merge

Use the pull-request REST endpoint's response as GitHub's merge verdict for
the authenticated bot; an aggregate `mergeStateStatus: BLOCKED` or
`mergeable_state: blocked` is not an API refusal. GitHub enforces the
preconditions itself — 409 when the head no longer matches `sha`, 405 when the
PR is closed or not mergeable. On a refusal, leave the PR open and report the
response. Never use auto-merge or omit `sha`: auto-merge stays enabled across
pushes by anyone with write access, so it can land a head no review read. A
review whose CI wait is off records the head instead, and tend-land starts a
landing session that makes this same pinned merge once CI is green
(**Without the CI wait** in `/tend-ci-runner:review`):

```bash
gh api "repos/{owner}/{repo}/pulls/<number>/merge" -X PUT \
  -f sha="$PINNED_SHA" -f merge_method=squash
```
