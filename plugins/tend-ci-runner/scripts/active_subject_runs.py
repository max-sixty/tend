# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Report the dedicated Tend runs still working a notification subject.

The notifications poll defers a thread a dedicated ``tend-*`` workflow already
owns.  Ownership has to cover runs GitHub has created but not started: a
``tend-mention`` run can sit ``queued`` for hours, and a poll that reads its
subject as unowned answers the maintainer a second time as the same bot
account.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import github_cli

# Every run status the Actions API accepts that is not `completed`. The API
# takes one status per request and answers an unknown one with an empty list
# rather than an error, so this list is pinned by tests instead of by a 400.
ACTIVE_STATUSES = ("queued", "in_progress", "waiting", "requested", "pending")

# Dedicated workflows only. The poll itself is `tend-notifications`, excluded
# below by run id rather than by name, since a second poll would own nothing.
WORKFLOW_PREFIX = "tend-"


def owning_runs(
    runs: list[dict[str, Any]], *, subject_title: str, own_run_id: int
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
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
