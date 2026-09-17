# Responding to a comment, an issue update, or a review

- [Read the thread](#read-the-thread)
- [A review's inline comments are a separate fetch](#a-reviews-inline-comments-are-a-separate-fetch)
- [Triggering issue/PR already closed](#triggering-issuepr-already-closed)
- [Whether to respond](#whether-to-respond)

## Read the thread

Read the full context before responding. The prompt provides a URL — extract the PR/issue number from it.

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

## A review's inline comments are a separate fetch

Neither `gh pr view --json reviews` nor `GET /pulls/<n>/reviews/<id>` returns a review's inline comments — both hand back the review body alone, with no field signalling that more exists, so a read that stops there looks complete. A one-line review body routinely sits on top of the maintainer's actual instructions. Whenever the trigger names a review ID, fetch them as part of reading context — not only when you already intend to reply inline:

```bash
gh api "repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/comments" \
  --jq '.[] | {id, path, line, body}'
```

An instruction found there constrains the whole response, including any code the reply quotes or carries into another PR.

For a review comment on a specific line (`[Comment on path:line]`), read that file and examine the code at that line before acting on it. When the GitHub API returns a `diff_hunk`, the reviewer's comment targets the **last line** of that hunk. Use this to disambiguate when multiple candidates exist nearby — match the reviewer's request against the specific anchored line, not the surrounding region.

## Triggering issue/PR already closed

If the trigger is a comment on an issue or PR and the target is **closed** by the time the job starts, the requested work was likely handled by a sibling run during the queue delay. Long `tend-mention` queues (hours, not minutes) make this common. Before starting work:

```bash
# For an issue trigger — check linked PRs that closed it.
gh issue view <number> --json state,closedAt,closedByPullRequestsReferences

# For a PR trigger — check whether the PR was merged.
gh pr view <number> --json state,mergedAt,mergeCommit
```

If a linked PR merged (or the triggering PR itself merged) **after the triggering comment was posted**, exit silently — the work is already on the default branch. If the closure looks unrelated (e.g. issue closed as not-planned with no merged PR), continue and address the comment normally.

## Whether to respond

**Your own prior comment.** The system prompt's self-loop guard exits silently when the trigger is the bot's own comment or review. One case falls outside it: a freshly-opened issue the bot authored with no prior bot comments (nightly failure, CI report, code-quality finding) is a report to act on, not a self-conversation — triage it normally. **Recheck before posting** in `posting.md` still prevents a duplicate triage comment if a sibling run fires on the same issue.

**Other participants.** Before responding, check how many distinct other participants are in the conversation.

- **Two-party** (you and one other participant): respond normally.
- **Multi-way** (multiple other participants): apply a stricter bar — only respond with concrete new information no one else provided: a code fix, reproduction, or specific technical detail.

Do not:
- Restate, agree with, or summarize what another participant just said
- Post "makes sense" or "good point" agreement comments
- Echo a user's findings back to them ("Good find!", "That's the smoking gun!")

A comment that responds to concerns you raised in a review is directed at you — briefly acknowledge resolution or explain why concerns remain.

If a maintainer has already addressed the point, exit silently unless you can add something they missed.
