#!/usr/bin/env python3
"""Fetches data for the tend website.

Produces two JSON files for the website to render:

  data/activity.json  recent activity across all tend bots
  data/stats.json     counts of lifetime activity across all tend bots

The third feature, "currently tending", is served by a Cloudflare Worker
(see ../worker/) — it needs sub-minute freshness, which static JSON can't
deliver. Don't add a currently-tending path here.

Inputs:
  data/consumers.json — list of {repo, bot_name} entries (one per repo
  running tend). Provides the bot identities the activity/stats queries
  iterate over.

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
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

GH_API = "https://api.github.com"
USER_AGENT = "tend-website-fetcher"
ACTIVITY_LIMIT = 10
# Search API: 30 req/min/token (and a stricter unwritten secondary limit).
# Stay safely under by sleeping ≥2.1 s between Search calls. Not applied to
# non-Search endpoints, which have a much higher (5000/h) ceiling.
SEARCH_MIN_INTERVAL_S = 2.1


# ---------------------------------------------------------------------------
# HTTP


_last_search_ts: float = 0.0


_RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4


def _gh_get(path: str, token: str | None) -> Any:
    """GET a GitHub REST API path; return parsed JSON.

    Self-throttles Search endpoints to stay under the 30 req/min limit.
    Retries with exponential backoff on transient failures (403 secondary
    rate limits, 429, 5xx, network errors). 4xx other than 403/429 fail
    immediately — they indicate a config problem the bot can't recover from.
    """
    global _last_search_ts
    last_err: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if path.startswith("/search/"):
            elapsed = time.monotonic() - _last_search_ts
            if elapsed < SEARCH_MIN_INTERVAL_S:
                time.sleep(SEARCH_MIN_INTERVAL_S - elapsed)
            _last_search_ts = time.monotonic()

        url = f"{GH_API}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_STATUSES:
                body = e.read().decode("utf-8", errors="replace")[:500]
                sys.exit(f"GitHub API {e.code} for {path}\n{body}")
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(2**attempt)  # 1s, 2s, 4s
    sys.exit(f"GitHub API persistent failure for {path}: {last_err}")


# Indirection so tests can inject responses without touching urllib.
_gh_get_fn: Callable[[str, str | None], Any] = _gh_get


def set_gh_get(fn: Callable[[str, str | None], Any]) -> None:
    """Override the GitHub HTTP function (used in tests)."""
    global _gh_get_fn
    _gh_get_fn = fn


# ---------------------------------------------------------------------------
# Consumers


def load_consumers(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        sys.exit(f"error: {path} not found")
    consumers = json.loads(path.read_text())
    if not isinstance(consumers, list):
        sys.exit(f"error: {path} must be a JSON array")
    for c in consumers:
        if not (
            isinstance(c, dict)
            and isinstance(c.get("repo"), str)
            and isinstance(c.get("bot_name"), str)
        ):
            sys.exit(f"error: {path} entries must be {{repo, bot_name}}; got {c!r}")
    return consumers


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
    bot_names: list[str],
    token: str | None,
    *,
    limit: int = ACTIVITY_LIMIT,
) -> dict[str, Any]:
    """Recent issues/PRs the bots have touched, grouped by activity kind.

    Runs three queries per bot (ci-fix / review / triage) and merges
    results. Each kind's results across bots are deduped by URL; first kind
    seen wins.
    """
    events: list[dict[str, str]] = []
    seen: set[str] = set()
    kinds: list[tuple[str, Callable[[str], str]]] = [
        ("ci-fix", lambda b: f"author:{b} is:pr"),
        ("review", lambda b: f"commenter:{b} is:pr -author:{b}"),
        ("triage", lambda b: f"commenter:{b} is:issue"),
    ]
    for kind, q_fn in kinds:
        for bot in bot_names:
            data = _search_issues(q_fn(bot), token, per_page=limit, sort="updated")
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


def fetch_stats(bot_names: list[str], token: str | None) -> dict[str, Any]:
    """Aggregate counters from Search API total_count, summed across bots.

    The Search API returns a count without paginating results, so each
    (stat × bot) is one cheap query.
    """
    week_ago = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
    stats: dict[str, Callable[[str], str]] = {
        "reviews_total": lambda b: f"commenter:{b} is:pr -author:{b}",
        "reviews_this_week": lambda b: (
            f"commenter:{b} is:pr -author:{b} updated:>={week_ago}"
        ),
        "ci_fixes_total": lambda b: f"author:{b} is:pr",
        "ci_fixes_this_week": lambda b: f"author:{b} is:pr updated:>={week_ago}",
        "triage_comments_total": lambda b: f"commenter:{b} is:issue",
    }
    out: dict[str, Any] = {"generated_at": _now_iso()}
    for key, q_fn in stats.items():
        total = 0
        for bot in bot_names:
            data = _search_issues(q_fn(bot), token, per_page=1)
            total += int(data.get("total_count", 0))
        out[key] = total
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
    p.add_argument(
        "--consumers-file",
        type=Path,
        default=Path("data/consumers.json"),
        help="Path to consumers.json (default: data/consumers.json)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data"),
        help="Where to write activity.json and stats.json (default: data/)",
    )
    args = p.parse_args(argv)

    consumers = load_consumers(args.consumers_file)
    bot_names = sorted({c["bot_name"] for c in consumers})
    if not bot_names:
        # Don't zero out the existing JSON if consumers.json is briefly empty
        # (transient discovery-script glitch). Better to keep yesterday's
        # numbers than emit all-zero stats.
        sys.exit("error: consumers.json has no entries — refusing to wipe existing data")

    token = os.environ.get("GITHUB_TOKEN") or None

    changed: list[Path] = []

    activity = fetch_activity(bot_names, token)
    out = args.out_dir / "activity.json"
    if write_if_changed(out, activity):
        changed.append(out)

    stats = fetch_stats(bot_names, token)
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
