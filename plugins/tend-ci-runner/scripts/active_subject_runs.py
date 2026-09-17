# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Report the dedicated Tend runs still working a notification subject.

The notifications poll defers a thread a dedicated ``tend-*`` workflow already
owns.  Ownership has to cover runs GitHub has created but not started: a
``tend-mention`` run can sit ``queued`` for hours, and a poll that reads its
subject as unowned answers the maintainer a second time as the same bot
account.  It also has to end: a run GitHub abandons stays non-terminal
forever, and an owner that never finishes defers its subject on every later
poll, so the thread is never handled at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import github_cli

# Every run status the Actions API accepts that is not `completed`. The API
# takes one status per request and answers an unknown one with an empty list
# rather than an error, so this list is pinned by tests instead of by a 400.
ACTIVE_STATUSES = ("queued", "in_progress", "waiting", "requested", "pending")

# Dedicated workflows only. The poll itself is `tend-notifications`, excluded
# below by run id rather than by name, since a second poll would own nothing.
WORKFLOW_PREFIX = "tend-"

# A run whose state has not moved in this long is never going to deliver.
# GitHub terminates a job queued past 24h, so nothing legitimate sits
# untouched for a day. Runs do get stranded non-terminal with no jobs and no
# further updates, and without this bound one of them pins its subject for
# good.
ABANDONED_AFTER = timedelta(hours=24)


def abandoned(run: dict[str, Any], *, now: datetime) -> bool:
    """Whether *run*'s state has stood still longer than a live run's ever does.

    Measured from `updated_at`, which GitHub advances on every job transition,
    rather than `created_at`, which spans the queue and the session both: a
    run queued most of a day and then running out its job timeout is over a
    day old while still live, and dropping it costs the second outward answer
    this check exists to prevent. A stranded run reports `updated_at` equal to
    its `created_at`, so the bound still reaches it.

    An unreadable or absent `updated_at` counts as fresh: deferring one extra
    poll costs a poll, while dropping a live owner costs a second outward
    answer from the same bot account.
    """
    touched = str(run.get("updated_at") or "")
    try:
        moved = datetime.fromisoformat(touched)
    except ValueError:
        return False
    if moved.tzinfo is None:
        moved = moved.replace(tzinfo=UTC)
    return now - moved > ABANDONED_AFTER


def owning_runs(
    runs: list[dict[str, Any]], *, subject_title: str, own_run_id: int, now: datetime
) -> list[dict[str, Any]]:
    """Reduce workflow runs to the dedicated ones handling *subject_title*.

    Matches on `display_title` because `workflow_run` does not expose the issue
    number for comment and review events.
    """
    owners: dict[int, dict[str, Any]] = {}
    for run in runs:
        run_id = int(run["id"])
        if run_id == own_run_id:
            continue
        if not str(run.get("name") or "").startswith(WORKFLOW_PREFIX):
            continue
        if run.get("display_title") != subject_title:
            continue
        if abandoned(run, now=now):
            continue
        owners.setdefault(
            run_id,
            {
                "id": run_id,
                "name": run["name"],
                "status": run.get("status"),
                "url": run.get("html_url"),
            },
        )
    return sorted(owners.values(), key=lambda run: run["id"])


def fetch_active_runs(repo: str) -> list[dict[str, Any]]:
    """Fetch one page per non-terminal status.

    Keeps the server-side `status=` filter rather than scanning the newest 100
    runs of any status: a long-queued run on a busy repository falls outside
    that window, and it is exactly the run this check exists to see.
    """
    runs: list[dict[str, Any]] = []
    for status in ACTIVE_STATUSES:
        response = github_cli.json_call(
            "api", f"repos/{repo}/actions/runs?status={status}&per_page=100"
        )
        runs.extend(response.get("workflow_runs") or [])
    return runs


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0]:
        print(f"usage: {sys.argv[0]} <subject-url>", file=sys.stderr)
        return 2
    repo = github_cli.repository()
    subject_title = github_cli.json_call("api", args[0])["title"]
    github_cli.dump(
        owning_runs(
            fetch_active_runs(repo),
            subject_title=subject_title,
            own_run_id=int(os.environ.get("GITHUB_RUN_ID") or 0),
            now=datetime.now(UTC),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
