# Dismissing a standing bot approval

Depth behind `/tend-ci-runner:running-in-ci`: read when you conclude that a PR the bot approved should not merge.

- [Dismiss a standing bot approval the moment you conclude the PR shouldn't merge](#dismiss-a-standing-bot-approval-the-moment-you-conclude-the-pr-shouldnt-merge)

## Dismiss a standing bot approval the moment you conclude the PR shouldn't merge

A bot `APPROVED` keeps deciding the PR until a dismissal or a `CHANGES_REQUESTED` replaces it. A later COMMENT doesn't, and neither does the event that actually invalidated it: another PR merging and superseding this one, a dependency bump that turns this PR into a downgrade, an approach the thread has since rejected. None of those touch the approved PR, so no review round fires and the approval stands indefinitely — with nothing between it and a merge once the branch stops conflicting. Whichever session reaches the conclusion is the one that has to clear it, whether or not this session posts anything.

Keying the dismissal to a post is what leaves it standing: **Recheck Before Posting** in `references/posting.md` rightly suppresses a second deferral comment when one already stands, and a dismissal that rides on that comment is suppressed with it. Dismiss on the conclusion, then say so in the summary — where this session does post a review carrying that conclusion, dismiss after the post lands, so a failed post doesn't leave the PR with neither a verdict nor findings.

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" \
  dismiss <number> "<what invalidated the approval>"
```

The test is the merge, not tidiness: a finding you'd have left as a review comment is no reason to withdraw a verdict the code still earns, and neither is a branch that merely can't merge yet — a conflicting PR whose code the approval still covers keeps it. Dismiss when merging the PR, once it could merge, would be the wrong outcome.
