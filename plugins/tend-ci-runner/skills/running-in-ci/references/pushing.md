# Pushing to a PR branch

Depth behind `/tend-ci-runner:running-in-ci`: read before `git push`, before merging the default branch into a PR branch, and before `gh pr close`, a revert, or a force-push.

- [Pushing to PR branches](#pushing-to-pr-branches)
- [Merging upstream into PR branches](#merging-upstream-into-pr-branches)
- [A terminal action collides with branch state, not comments](#a-terminal-action-collides-with-branch-state-not-comments)

## Pushing to PR branches

Always use `git push` without specifying a remote — `gh pr checkout` configures tracking to the correct remote, including for fork PRs. Specifying `origin` explicitly can push to the wrong place.

If pushing fails (fork PR with edits disabled), fall back to posting code snippets in a comment. Don't reference commit SHAs from temporary branches — post code inline.

### Batch the push — every push costs a reviewer round

`tend-review` triggers on `synchronize` under a per-PR concurrency group with `queue: max` and without cancel-in-progress: pending PR events within GitHub's queue limit wait while a review session runs. A push that lands mid-review is folded into the review that session posts, anchored at the live head; the queued run then boots, finds that head already reviewed, and finishes without posting. Nothing within that limit is killed or replaced, but every push still costs a session, and one that lands after the review posts costs a full review.

- **Commit everything before `gh pr create`.** Changelog entries, test pins, and formatting fixups belong in the initial push, not a follow-up thirty seconds later.
- **Make the commits, then push once** — not a push after each commit. Amends and rebases count: a force-push fires `synchronize` too.

A follow-up push that acts on information the session didn't have at push time — review feedback, a red check — earns its round. What's wasteful is splitting work you already have into several pushes.

### Re-check PR state before pushing a follow-up commit

Any wait that lets time pass — a CI poll, coverage fetch, sleep, background task — also gives a maintainer time to merge or close the PR. After waiting:

```bash
STATE=$(gh pr view <N> --json state --jq '.state')
[ "$STATE" = "OPEN" ] || { echo "PR #<N> is $STATE — skipping push"; exit 0; }
```

If the PR is merged, the work is superseded. Comment if a real gap remains; do not push to the now-orphan branch. After merge, `gh pr view <N> --json headRefOid` returns the SHA at merge time and never advances — polling it for a new push is a guaranteed deadlock.

### Re-check the head SHA before the expensive verify, not just before the push

A PR another tend session opened keeps that session alive polling its checks, and closing a red gate is exactly the follow-up it stays alive for — so a sibling commit can land on the branch while you edit it. Find that out at `git push` and the suite you just ran was scoped against a stale head, so the whole verify cycle is paid again after the rebase. Record the head before editing and re-check it immediately before each expensive step — full test suite, coverage or snapshot regeneration, a long build:

```bash
HEAD_OID=$(gh pr view <N> --json headRefOid --jq '.headRefOid')
# ...edits...
read -r NOW_OID NOW_STATE < <(gh pr view <N> --json headRefOid,state --jq '"\(.headRefOid) \(.state)"')
[ "$NOW_OID" = "$HEAD_OID" ] && [ "$NOW_STATE" = "OPEN" ] \
  || echo "sibling pushed or PR closed — fetch and re-scope before verifying"
```

`state` rides along on the same call because a merged or closed PR freezes `headRefOid` at its merge-time value (see above) — the OID comparison alone passes and the expensive step runs on work that is already superseded. On a non-`OPEN` state, stop per the subsection above rather than re-scoping.

If it moved, `git fetch` and read the new commits before verifying: drop whatever the sibling already landed, rebase what's left, and verify once against the new head. Expect the overlap rather than treating it as a surprise — a reviewer and a coverage gate reading the same new code ask for the same missing test. The runs API can't substitute for this check: a `schedule` or `repository_dispatch` run reports `head_branch: main`, not the branch it is editing, so a live sibling is invisible there.

## Merging upstream into PR branches

When merging the default branch into a PR branch, **never use `--allow-unrelated-histories`**: if `git merge` fails because no merge base exists, the checkout is broken (usually shallow — re-checkout with `fetch-depth: 0`), and forcing the merge creates add/add conflicts in every file. If the merge fails because untracked files would be overwritten, stash them (`git stash --include-untracked`, merge, `git stash pop`) rather than deleting them.

## A terminal action collides with branch state, not comments

The pre-post re-fetch in `references/posting.md` counts comments and reviews, because that is what a duplicate *post* collides with. Closing a PR, reverting it, or force-pushing over it collides with **commits** instead, and a sibling session's pushed, CI-green commit is invisible to all three checks a session typically runs first: a comments-and-reviews re-fetch, the `state == OPEN` check under **Re-check PR state before pushing a follow-up commit**, and a re-read of the review bodies that prompted the action. `--delete-branch` turns that blind spot destructive — the branch ref goes and the commit survives only through the PR ref.

So before `gh pr close`, a revert, or a force-push, re-read the branch itself rather than the thread:

```bash
gh pr view <N> --json headRefOid,commits,comments,reviews \
  --jq '{head: .headRefOid, commits: [.commits[].oid],
         comments: (.comments | length), reviews: (.reviews | length)}'
```

If the head moved past the SHA you last pushed, a sibling acted on this PR while you waited — read its commits before deciding. Usually it applied one of the remedies you were weighing, which changes what the close is *for*, not whether to close: a PR whose premise a review invalidated is still yours to withdraw, and the session holding the PR is the one that can. Say what the sibling landed and why the close stands anyway, so the thread reads as one decision instead of two contradictory ones, and drop `--delete-branch` so that work stays reachable.
