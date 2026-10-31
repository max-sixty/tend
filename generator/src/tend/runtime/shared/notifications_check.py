"""Decide whether the notifications workflow has work for an agent."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

from tend.runtime.shared import _common

GRAPHQL_QUERY = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 100) {
    nodes { ... on PullRequest {
      number baseRefName mergeable headRefOid
      comments(last: 100) { nodes { author { login } body } }
    } }
  }
}
"""


def _paginated(path: str) -> list[Any]:
    # `--slurp` prints one array holding every page.
    pages = _common.gh_json("api", "--paginate", "--slurp", path, quiet=True)
    if not all(isinstance(page, list) for page in pages):
        raise TypeError("paginated GitHub response was not an array")
    return [item for page in pages for item in page]


def _output(name: str, value: str | int) -> None:
    _common.set_output(name, str(value))


def _notifications(cutoff: str) -> int:
    try:
        return len(_paginated(f"notifications?before={cutoff}"))
    except (json.JSONDecodeError, subprocess.CalledProcessError, TypeError):
        print("::warning::notifications fetch failed; queue left for the next cycle")
        return 0


def _actor_login(actor: object) -> str:
    if not isinstance(actor, dict):
        return ""
    return str(actor.get("login") or "")


def _is_deferred(pr: dict[str, Any], bot: str) -> bool:
    marker = f"<!-- tend-conflict-deferred head={pr.get('headRefOid', '')} -->"
    comments = pr.get("comments")
    nodes = comments.get("nodes", []) if isinstance(comments, dict) else []
    return any(
        _actor_login(comment.get("author")) == bot
        and str(comment.get("body") or "").rstrip().split("\n")[-1] == marker
        for comment in nodes
        if isinstance(comment, dict)
    )


def _merge_conflicts(repo: str, pr: dict[str, Any]) -> bool:
    """Suppress only a verified clean merge of freshly fetched base and head.

    A disposable bare repository avoids checkout changes, hooks and consumer
    git config. Fetch full ancestry so a shallow checkout cannot hide a merge
    base. No persistent clean marker is needed: both refs are checked each poll.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="tend-conflict-") as directory:

            def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["git", "-C", directory, *args],
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=60,
                )

            git("init", "--bare")
            git(
                "fetch",
                "--no-tags",
                "--",
                f"https://github.com/{repo}.git",
                f"refs/heads/{pr['baseRefName']}:refs/heads/base",
                f"refs/pull/{int(pr['number'])}/head:refs/heads/head",
            )
            result = git(
                "merge-tree",
                "--write-tree",
                "refs/heads/base",
                "refs/heads/head",
                check=False,
            )
            if result.returncode in (0, 1):
                return result.returncode == 1
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        pass
    # Conflicts and unavailable verification both warrant an agent's inspection.
    print("::warning::local merge was not verified clean; retaining conflict work")
    return True


def _conflicts(repo: str) -> int:
    try:
        bot = _common.gh("api", "user", "--jq", ".login", quiet=True).strip()
        if not bot:
            raise ValueError("authenticated GitHub login was empty")
        response = _common.gh_json(
            "api",
            "graphql",
            "-f",
            f"query={GRAPHQL_QUERY}",
            "-f",
            f"q=repo:{repo} author:{bot} is:pr is:open",
            quiet=True,
        )
        nodes = response["data"]["search"]["nodes"]
        if not isinstance(nodes, list):
            raise TypeError("GraphQL search nodes were not an array")
        # `mergeable` is computed lazily: the first read after the base branch
        # moves returns `UNKNOWN` and enqueues the computation, so counting
        # anything but a settled `CONFLICTING` boots a session after every merge
        # into the default branch. A conflict behind an `UNKNOWN` is counted on
        # the next poll, which reads the value this one enqueued; the resolver
        # test-merges locally either way.
        return sum(
            pr.get("mergeable") == "CONFLICTING"
            and not _is_deferred(pr, bot)
            and _merge_conflicts(repo, pr)
            for pr in nodes
            if isinstance(pr, dict)
        )
    except (
        json.JSONDecodeError,
        KeyError,
        subprocess.CalledProcessError,
        TypeError,
        ValueError,
    ):
        print("::warning::bot PR conflict scan failed; retrying next cycle")
        return 0


#: How old a notification must be before this poll takes it. The event run a
#: notification started usually finishes, and marks it read, within this, so a
#: poll doesn't boot a session only to find that run still holds its subject.
GRACE = timedelta(minutes=10)


def main(*, now: datetime | None = None) -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    cutoff = ((now or datetime.now(UTC)) - GRACE).strftime("%Y-%m-%dT%H:%M:%SZ")
    _output("cutoff", cutoff)

    try:
        _common.gh(
            "api",
            f"repos/{repo}/subscription",
            "-X",
            "PUT",
            "-F",
            "subscribed=true",
            "-F",
            "ignored=false",
            "--silent",
            quiet=True,
        )
    except subprocess.CalledProcessError:
        print("::warning::could not enable repository watching; retrying next cycle")

    count = _notifications(cutoff)
    _output("count", count)
    conflict_count = _conflicts(repo)
    _output("conflict_count", conflict_count)

    if count == 0 and conflict_count == 0:
        print("No notification or conflict work — skipping")
    else:
        if count:
            print(f"{count} notification task(s) — proceeding")
        if conflict_count:
            print(f"{conflict_count} possible conflicted bot PR(s) — proceeding")
    return 0
