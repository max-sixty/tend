"""Expose a PR's canonical bot review state and discussion context.

Feedback retains authors and discussion context for threads the bot participates in.
Consumers derive unresolved bot-started findings from that same thread snapshot.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from tend.runtime.shared import _common

DRAFT_REVIEW_MARKER = "<!-- tend:draft-review -->"
LEGACY_DRAFT_REVIEW_PREFIX = "Reviewing as a draft —"
# `gh api graphql --paginate` follows `$endCursor` through every thread page.
THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 100) {
            nodes { author { login } path line body createdAt commit { oid } }
          }
        }
      }
    }
  }
}
"""
RESOLVE_THREAD_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id }
  }
}
"""


def review_state(
    *,
    head_sha: str,
    bot: str,
    substantive_ids: set[int],
    force_push_times: list[str],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reduce GitHub review records to the state used by Tend's skills."""
    mine = [
        review
        for review in reviews
        if _common.actor_login(review.get("user")) == bot
        and review.get("submitted_at") is not None
    ]
    substantive = [
        review
        for review in mine
        if review.get("body")
        or review.get("id") in substantive_ids
        or review.get("state") in {"APPROVED", "CHANGES_REQUESTED"}
    ]
    last_substantive = substantive[-1] if substantive else None
    last_force_push_at = max(force_push_times, default="")

    def after_rewrite(review: dict[str, Any]) -> bool:
        return (
            not last_force_push_at
            or (review.get("submitted_at") or "") > last_force_push_at
        )

    at_head = [
        review
        for review in substantive
        if review.get("commit_id") == head_sha and after_rewrite(review)
    ]
    body_at_head = [review for review in at_head if review.get("body")]
    decisions = [
        review
        for review in mine
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED"}
    ]
    standing_approval = (
        decisions[-1]
        if decisions and decisions[-1].get("state") == "APPROVED"
        else None
    )

    def is_draft_review(review: dict[str, Any]) -> bool:
        body = review.get("body") or ""
        # TODO(2026-12-01): Drop the prose-prefix fallback after pre-marker
        # reviews have aged out.
        return review.get("state") == "COMMENTED" and (
            DRAFT_REVIEW_MARKER in body or body.startswith(LEGACY_DRAFT_REVIEW_PREFIX)
        )

    return {
        "reviews": [
            {
                "id": review["id"],
                "sha": review["commit_id"],
                "state": review["state"],
                "at": review["submitted_at"],
                "body": review["body"],
                "draft_mode": is_draft_review(review),
            }
            for review in mine
        ],
        "head_sha": head_sha,
        "bot_login": bot,
        "last_force_push_at": last_force_push_at,
        "last_substantive": (
            {
                "id": last_substantive["id"],
                "sha": last_substantive["commit_id"],
                "state": last_substantive["state"],
                "at": last_substantive.get("submitted_at"),
                "draft_mode": is_draft_review(last_substantive),
            }
            if last_substantive
            else None
        ),
        "force_pushed_since": bool(
            last_substantive
            and last_force_push_at
            and last_force_push_at > (last_substantive.get("submitted_at") or "")
        ),
        "at_head": (
            {
                "id": at_head[-1]["id"],
                "state": at_head[-1]["state"],
                "at": at_head[-1].get("submitted_at"),
                "draft_mode": is_draft_review(at_head[-1]),
            }
            if at_head
            else None
        ),
        "orphan_id": body_at_head[-1]["id"] if body_at_head else None,
        "fresh_approval_sha": (
            (standing_approval.get("commit_id") or "")
            if standing_approval and after_rewrite(standing_approval)
            else ""
        ),
        "stale_approval_id": (
            standing_approval["id"]
            if standing_approval
            and last_force_push_at
            and (standing_approval.get("submitted_at") or "") < last_force_push_at
            else ""
        ),
        "standing_approval_id": (standing_approval["id"] if standing_approval else ""),
    }


def fetch_review_state(pr: str, *, repo: str | None = None) -> dict[str, Any]:
    """Fetch and reduce the review state for *pr*."""
    repo = repo or _common.repository()
    bot = str(_common.gh_json("api", "user")["login"])
    head = _common.gh_json("pr", "view", pr, "--repo", repo, "--json", "headRefOid")[
        "headRefOid"
    ]
    comments = _common.gh_paginated(f"repos/{repo}/pulls/{pr}/comments")
    substantive_ids = {
        int(comment["pull_request_review_id"])
        for comment in comments
        if comment.get("in_reply_to_id") is None
        and comment.get("pull_request_review_id") is not None
    }
    timeline = _common.gh_paginated(f"repos/{repo}/issues/{pr}/timeline")
    force_push_times = [
        event["created_at"]
        for event in timeline
        if event.get("event") == "head_ref_force_pushed"
    ]
    reviews = _common.gh_paginated(f"repos/{repo}/pulls/{pr}/reviews")
    return review_state(
        head_sha=head,
        bot=bot,
        substantive_ids=substantive_ids,
        force_push_times=force_push_times,
        reviews=reviews,
    )


def dismiss_standing_approval(pr: str, message: str) -> None:
    """Dismiss the bot approval currently deciding *pr*, when one exists."""
    repo = _common.repository()
    state = fetch_review_state(pr, repo=repo)
    review_id = state["standing_approval_id"]
    if review_id:
        _common.gh(
            "api",
            f"repos/{repo}/pulls/{pr}/reviews/{review_id}/dismissals",
            "-X",
            "PUT",
            "-f",
            f"message={message}",
        )


def prepare_approval(pr: str) -> dict[str, Any]:
    """Pin the live head unless the bot already approved that exact commit."""
    state = fetch_review_state(pr)
    head_sha = str(state["head_sha"])
    pin = Path(os.environ.get("CHECKED_HEAD_DIR", tempfile.gettempdir())) / (
        f"checked-head-{pr}"
    )
    pin.unlink(missing_ok=True)
    already_approved = state.get("fresh_approval_sha") == head_sha
    result = {"head_sha": head_sha, "already_approved": already_approved}
    _common.dump(result)
    sys.stdout.flush()
    if not already_approved:
        pin.write_text(f"{head_sha}\n")
    return result


def dismiss_stale_approval(pr: str, message: str) -> None:
    """Dismiss the approval that predates the PR's newest force push, if any."""
    repo = _common.repository()
    state = fetch_review_state(pr, repo=repo)
    review_id = state["stale_approval_id"]
    if review_id:
        _common.gh(
            "api",
            f"repos/{repo}/pulls/{pr}/reviews/{review_id}/dismissals",
            "-X",
            "PUT",
            "-f",
            f"message={message}",
        )


def _review_threads(pr: str, repo: str) -> list[dict[str, Any]]:
    owner, name = repo.split("/", 1)
    pages = _common.gh_pages(
        "graphql",
        "-f",
        f"query={THREADS_QUERY}",
        "-f",
        f"owner={owner}",
        "-f",
        f"repo={name}",
        "-F",
        f"number={pr}",
    )
    return [
        thread
        for page in pages
        for thread in page["data"]["repository"]["pullRequest"]["reviewThreads"][
            "nodes"
        ]
    ]


def feedback(pr: str) -> dict[str, Any]:
    """Return canonical review state with conversation and contextual bot findings."""
    repo = _common.repository()
    state = fetch_review_state(pr, repo=repo)
    bot = state["bot_login"]
    pr_view = _common.gh_json("pr", "view", pr, "--repo", repo, "--json", "comments")
    return {
        "review_state": state,
        "conversation": [
            {
                "author": _common.actor_login(comment.get("author")),
                "created_at": comment.get("createdAt"),
                "body": comment.get("body"),
            }
            for comment in pr_view["comments"]
        ],
        "review_threads": [
            {
                "id": thread["id"],
                "is_resolved": thread["isResolved"],
                "is_outdated": thread["isOutdated"],
                "comments": [
                    {
                        "author": _common.actor_login(comment.get("author")),
                        "sha": comment["commit"]["oid"] if comment["commit"] else None,
                        "path": comment["path"],
                        "line": comment["line"],
                        "created_at": comment["createdAt"],
                        "body": comment["body"],
                    }
                    for comment in thread["comments"]["nodes"]
                ],
            }
            for thread in _review_threads(pr, repo)
            if any(
                _common.actor_login(comment.get("author")) == bot
                for comment in thread["comments"]["nodes"]
            )
        ],
    }


def resolve_thread(thread_id: str) -> None:
    """Resolve one review thread by its GraphQL node id."""
    _common.gh(
        "api",
        "graphql",
        "-f",
        f"query={RESOLVE_THREAD_MUTATION}",
        "-f",
        f"threadId={thread_id}",
    )
