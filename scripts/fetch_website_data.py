#!/usr/bin/env python3
"""Fetches data for the tend website.

Produces two JSON files for the website to render:

  static/data/activity.json  recent tend bot activity
  static/data/stats.json     counts of tend's lifetime activity

The third feature, "currently tending", is served by a Cloudflare Worker
(see ../worker/) — it needs sub-minute freshness, which static JSON can't
deliver. Don't add a currently-tending path here.

Auth:
  GITHUB_TOKEN env var. Required (Search API is unusable unauthenticated). In
  GitHub Actions, the default workflow token works for public repos.
"""

# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

GH_API = "https://api.github.com"
USER_AGENT = "tend-website-fetcher"
DEFAULT_BOT_NAME = "tend-agent"
ACTIVITY_LIMIT = 10


# ---------------------------------------------------------------------------
# HTTP


def _gh_get(path: str, token: str | None) -> Any:
    """GET a GitHub REST API path; return parsed JSON."""
    url = f"{GH_API}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# Indirection so tests can inject responses without touching urllib.
_gh_get_fn: Callable[[str, str | None], Any] = _gh_get


def set_gh_get(fn: Callable[[str, str | None], Any]) -> None:
    """Override the GitHub HTTP function (used in tests)."""
    global _gh_get_fn
    _gh_get_fn = fn


# ---------------------------------------------------------------------------
# Activity feed


def _search_issues(
    query: str,
    token: str | None,
    *,
    per_page: int = 1,
    sort: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {"q": query, "per_page": str(per_page)}
    if sort:
        params["sort"] = sort
        params["order"] = "desc"
    return _gh_get_fn(f"/search/issues?{urllib.parse.urlencode(params)}", token)


def _repo_from_url(repository_url: str) -> str:
    # https://api.github.com/repos/owner/name -> owner/name
    return repository_url.split("/repos/", 1)[1]


def fetch_activity(
    bot_name: str,
    token: str | None,
    *,
    limit: int = ACTIVITY_LIMIT,
) -> dict[str, Any]:
    """Recent issues/PRs the bot has touched, grouped by activity kind."""
    queries: list[tuple[str, str]] = [
        (f"author:{bot_name} is:pr", "ci-fix"),
        (f"commenter:{bot_name} is:pr -author:{bot_name}", "review"),
        (f"commenter:{bot_name} is:issue", "triage"),
    ]
    events: list[dict[str, str]] = []
    seen: set[str] = set()
    for q, kind in queries:
        data = _search_issues(q, token, per_page=limit, sort="updated")
        for item in data.get("items", []):
            url = item["html_url"]
            if url in seen:
                continue
            seen.add(url)
            events.append(
                {
                    "repo": _repo_from_url(item["repository_url"]),
                    "kind": kind,
                    "title": item["title"],
                    "url": url,
                    "at": item["updated_at"],
                }
            )
    events.sort(key=lambda x: x["at"], reverse=True)
    return {
        "generated_at": _now_iso(),
        "events": events[:limit],
    }


# ---------------------------------------------------------------------------
# Stats


def fetch_stats(bot_name: str, token: str | None) -> dict[str, Any]:
    """Aggregate counters from Search API total_count.

    The Search API returns a count without paginating results, so each stat
    is one cheap query.
    """
    week_ago = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
    queries: dict[str, str] = {
        "reviews_total": f"commenter:{bot_name} is:pr -author:{bot_name}",
        "reviews_this_week": (
            f"commenter:{bot_name} is:pr -author:{bot_name} updated:>={week_ago}"
        ),
        "ci_fixes_total": f"author:{bot_name} is:pr",
        "ci_fixes_this_week": f"author:{bot_name} is:pr updated:>={week_ago}",
        "triage_comments_total": f"commenter:{bot_name} is:issue",
    }
    out: dict[str, Any] = {"generated_at": _now_iso()}
    for key, q in queries.items():
        data = _search_issues(q, token, per_page=1)
        out[key] = int(data.get("total_count", 0))
    return out


# ---------------------------------------------------------------------------
# Output


def write_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    """Write payload to path; return True if file content actually changed.

    Compares structural content (excluding `generated_at`) against any
    existing file so we don't churn commits when stats haven't moved.
    """
    new_structural = _structural(payload)
    if path.exists():
        existing = json.loads(path.read_text())
        if _structural(existing) == new_structural:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return True


def _structural(payload: dict[str, Any]) -> str:
    return json.dumps(
        {k: v for k, v in payload.items() if k != "generated_at"},
        sort_keys=True,
    )


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Entry


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--out-dir", type=Path, default=Path("static/data"))
    p.add_argument("--bot-name", default=DEFAULT_BOT_NAME)
    args = p.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or None

    changed: list[Path] = []

    activity = fetch_activity(args.bot_name, token)
    out = args.out_dir / "activity.json"
    if write_if_changed(out, activity):
        changed.append(out)

    stats = fetch_stats(args.bot_name, token)
    out = args.out_dir / "stats.json"
    if write_if_changed(out, stats):
        changed.append(out)

    if changed:
        print("changed:")
        for c in changed:
            print(f"  {c}")
    else:
        print("no changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
