"""Decide whether the notifications workflow has work for an agent."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

from tend.runtime.ci import active_subject_runs
from tend.runtime.shared import _common

GRAPHQL_QUERY = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 100) {
    nodes { ... on PullRequest {
      mergeable headRefOid
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


def _notifications(cutoff: str) -> list[Any]:
    try:
        return _paginated(f"notifications?before={cutoff}")
    except (json.JSONDecodeError, subprocess.CalledProcessError, TypeError):
        print("::warning::notifications fetch failed; queue left for the next cycle")
        return []


def _subject(notification: object, repo: str) -> tuple[str, int] | None:
    """The title and number of a same-repository issue or PR subject."""
    if not isinstance(notification, dict):
        return None
    if (notification.get("repository") or {}).get("full_name") != repo:
        return None
    subject = notification.get("subject") or {}
    url = str(subject.get("url") or "")
    head, _, tail = url.rpartition("/")
    if not tail.isdigit() or head.rpartition("/")[2] not in ("issues", "pulls"):
        return None
    return str(subject.get("title") or ""), int(tail)


def _unowned(repo: str, notifications: list[Any], now: datetime) -> int:
    """Count notifications no dedicated Tend run still holds.

    A session defers a subject that `active-subject-runs` lists, so booting one
    for it while a long review or triage runs only pays for that deferral. The
    session's check stays the authority: an unreadable runs listing, or a
    subject this can't match, counts as work.
    """
    subjects = [_subject(notification, repo) for notification in notifications]
    if not any(subjects):
        return len(notifications)
    try:
        runs = active_subject_runs.fetch_active_runs(repo)
        job_subjects = active_subject_runs.fetch_job_subjects(repo, runs)
    except (*_common.GH_READ_FAILED, KeyError, TypeError, ValueError):
        print("::warning::active run listing failed; counting every notification")
        return len(notifications)
    own_run_id = int(os.environ.get("GITHUB_RUN_ID") or 0)
    return sum(
        subject is None
        or not active_subject_runs.subject_runs(
            runs,
            subject_title=subject[0],
            own_run_id=own_run_id,
            now=now,
            job_subjects=job_subjects,
            subject_number=subject[1],
        )
        for subject in subjects
    )


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
            pr.get("mergeable") == "CONFLICTING" and not _is_deferred(pr, bot)
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
    now = now or datetime.now(UTC)
    cutoff = (now - GRACE).strftime("%Y-%m-%dT%H:%M:%SZ")
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

    notifications = _notifications(cutoff)
    count = _unowned(repo, notifications, now) if notifications else 0
    if owned := len(notifications) - count:
        print(f"{owned} notification(s) held by a running Tend workflow")
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
