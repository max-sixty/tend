---
name: mention
description: Handle a mention or follow-up on an issue or PR where the bot participated.
metadata:
  internal: true
---

# Handle Thread Activity

Read the triggering activity and live thread, then carry out the outstanding
request with the maintainer's latest constraints.

## Required skills

- Load `/tend-ci-runner:run-tend` and its repo-specific overlay first.
- Load `/tend-ci-runner:respond-on-thread` before acting and
  `/tend-ci-runner:post-to-github` before replying.
- Load `/tend-ci-runner:fix-a-bug` before fixing, `/tend-ci-runner:push-commits`
  before pushing, and `/tend-ci-runner:monitor-ci` after a push.

## Handle current work

Use `/tend-ci-runner:respond-on-thread` to read the full context, including
inline review comments and unfinished work on the bot's own PRs. Handle other
unaddressed requests oldest first; a queued event can have been replaced while
the preceding run worked.

Respond to activity directed at the bot, questions it can help with, or changes
it can make. A plain approval or a conversation between other participants
needs no reply. Apply **Recheck before posting** in
`/tend-ci-runner:post-to-github` to the response; a prior reply suppresses a
duplicate post while outstanding work is still handled.
