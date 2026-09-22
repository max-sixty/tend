---
name: check-requester-access
description: Check a requester's access tier. Use when a request aims you at someone else's work — close, lock, label, revert, dismiss, push.
metadata:
  internal: true
---

# Checking a requester's access

## Helping vs. directing

Anyone can ask for help with a problem they raise: investigating a bug, answering a question, creating an issue or PR to address it. These are proposals — a maintainer still decides what to merge or act on.

Directing the bot to affect someone else's work — closing, reopening, or locking issues/PRs, dismissing reviews, reverting commits, applying or removing labels, pushing commits to a PR owned by another author — requires Maintainer-tier access. Before complying, check the requester's `author_association`:

Read `references/author-association.md` for the tiers.

For Maintainer-tier requesters, proceed. For anyone else, briefly explain that a maintainer needs to make that call.

The test: "Am I helping this person with something they raised, or following a directive that affects someone else's work?"

This follows the repo > bundled rule from **First Steps** in `/tend-ci-runner:run-tend`. If a repo's `running-tend` skill explicitly authorizes an action (e.g., closing duplicate issues during triage), follow the repo-specific instruction.
