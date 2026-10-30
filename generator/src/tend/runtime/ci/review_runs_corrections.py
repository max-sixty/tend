"""Collect maintainer corrections made during a review-runs window."""

from __future__ import annotations

import sys
from typing import Any
from urllib.parse import quote

from tend.runtime.shared import _common

#: GitHub search returns at most 1000 results, so both searches below read to
#: the API's own ceiling rather than a guessed count.
SEARCH_LIMIT = 1000


def correction_report(
    *,
    since: str,
    bot: str,
    bot_prs: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the stable JSON shape consumed by the review-runs skill."""
    return {
        "since": since,
        "bot": bot,
        "dispositions": [
            pr for pr in bot_prs if pr.get("closedAt") and pr["closedAt"] > since
        ],
        "comments": [
            {
                "author": author,
                "created": comment["created_at"],
                "updated": comment["updated_at"],
                "url": comment["html_url"],
                "body": (comment.get("body") or "")[:300],
            }
            for comment in comments
            if (author := _common.actor_login(comment.get("user"))) != bot
        ],
        "reviews": [
            {
                "author": author,
                "at": review["submitted_at"],
                "state": review["state"],
                "url": review["html_url"],
                "body": review["body"][:300],
            }
            for review in reviews
            if (author := _common.actor_login(review.get("user"))) != bot
            and review.get("submitted_at", "") >= since
            and review.get("body")
        ],
    }


def main(since: str) -> int:
    repo = _common.repository()
    bot = str(_common.gh_json("api", "user").get("login") or "")
    if not bot:
        print("could not resolve the bot login from `gh api user`", file=sys.stderr)
        return 2

    bot_prs = _common.gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--author",
        bot,
        "--state",
        "all",
        "--limit",
        str(SEARCH_LIMIT),
        # Selected by closure, not recency: the newest bot PRs by creation
        # miss an older one a maintainer closed inside the window.
        "--search",
        f"closed:>={since}",
        "--json",
        "number,title,state,closedAt",
    )
    encoded_since = quote(since, safe="-TZ")
    comments = [
        comment
        for endpoint in ("issues", "pulls")
        for comment in _common.gh_paginated(
            f"repos/{repo}/{endpoint}/comments?since={encoded_since}&per_page=100",
        )
    ]

    candidates = _common.gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        str(SEARCH_LIMIT),
        "--search",
        f"updated:>={since}",
        "--json",
        "number",
    )
    reviews = [
        review
        for candidate in candidates
        for review in _common.gh_paginated(
            f"repos/{repo}/pulls/{candidate['number']}/reviews?per_page=100",
        )
    ]

    _common.dump(
        correction_report(
            since=since,
            bot=bot,
            bot_prs=bot_prs,
            comments=comments,
            reviews=reviews,
        )
    )
    return 0
