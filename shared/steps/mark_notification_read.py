"""Mark the notification thread for the triggering event read.

The scheduled ``tend-notifications`` poll otherwise burns tokens rediscovering
work this run already did. Shared verbatim by both harness actions, which gate
the step on a successful run (``if: success()``).

Decisions this encodes:

- A thread is marked only when its ``updated_at`` predates this run's start.
  Activity that arrived mid-run is what the next workflow run has to see, so
  it stays unread. GitHub applies the cutoff: the repository's unread threads
  are read with ``before=run_started_at``, which excludes the instant itself,
  and every page of them, since a busy inbox outgrows the first.
- Without ``run_started_at`` that cutoff cannot be set, and marking
  unconditionally would swallow exactly the mid-run activity the guard exists
  to preserve — so a failed or absent timestamp skips this cycle and leaves
  the thread to the scheduled poll.
- The agent run already succeeded, so nothing here may fail the step: a
  transient API error warns and returns 0.
- ``issue_comment`` fires for both issues and PR conversation comments, but a
  PR notification's ``subject.url`` always names ``/pulls/N``; the issue's
  ``pull_request`` field is what tells the two apart.
- Review events reach tend-mention re-posted by tend-mention-relay as
  ``repository_dispatch``, whose ``client_payload.pr`` names the PR.

Inputs (env): ``GITHUB_EVENT_NAME``, ``GITHUB_EVENT_PATH``,
``GITHUB_REPOSITORY``, ``GITHUB_RUN_ID`` (from Actions), plus the bot's
``GITHUB_TOKEN``, which reaches ``gh`` through the environment.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import _common


def subject_url(repo: str) -> str | None:
    """The notification ``subject.url`` for the triggering event.

    ``None`` for an event that names no single issue or PR, which is nothing
    to mark read — and equally for one that should name a number but does not,
    since this step runs only after the agent already succeeded and a payload
    shape it cannot read must not turn that green run red.
    """
    number = _common.subject_number()
    if number is None:
        return None
    event_name = os.environ["GITHUB_EVENT_NAME"]
    on_an_issue = event_name == "issues" or (
        event_name == "issue_comment"
        and not _common.dig(_common.event_payload(), "issue", "pull_request")
    )
    kind = "issues" if on_an_issue else "pulls"
    return f"https://api.github.com/repos/{repo}/{kind}/{number}"


def threads_to_mark(notifications: list[Any], url: str) -> list[str]:
    """The ids of the threads whose subject is *url*."""
    return [
        str(notification["id"])
        for notification in notifications
        if isinstance(notification, dict)
        and _common.dig(notification, "subject", "url") == url
        and notification.get("id") is not None
    ]


def main() -> int:
    env = _common.require_env(
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
    )
    repo = env["GITHUB_REPOSITORY"]

    url = subject_url(repo)
    if url is None:
        return 0

    run_id = env["GITHUB_RUN_ID"]
    try:
        run = _common.gh_json("api", f"repos/{repo}/actions/runs/{run_id}")
    except _common.GH_READ_FAILED:
        run = None
    run_started_at = run.get("run_started_at") if isinstance(run, dict) else None
    if not isinstance(run_started_at, str) or not run_started_at:
        _common.annotate(
            "warning",
            "Could not read run_started_at; leaving notification unread (non-fatal)",
        )
        return 0

    # An unreachable API, or a 200 carrying HTML or an error object rather than
    # the inbox, is the same non-fatal outcome: warn and leave the thread.
    try:
        notifications = _common.gh_paginated(
            f"repos/{repo}/notifications?before={run_started_at}&per_page=50"
        )
    except _common.GH_READ_FAILED:
        _common.annotate("warning", "Failed to mark notification as read (non-fatal)")
        return 0

    for thread_id in threads_to_mark(notifications, url):
        try:
            _common.gh("api", f"notifications/threads/{thread_id}", "-X", "PATCH")
        except subprocess.CalledProcessError:
            continue
    return 0


if __name__ == "__main__":
    _common.run(main)
