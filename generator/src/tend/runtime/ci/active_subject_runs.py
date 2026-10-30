"""Report active dedicated Tend run candidates for a notification subject.

The report preserves each workflow's path and run status without classifying
independent ownership. Include runs GitHub has created but not started:
``tend-mention`` can sit ``queued`` for hours, and overlooking it can make a
notifications poll answer the same maintainer twice. Exclude abandoned runs
whose unchanged non-terminal state would otherwise defer the subject forever.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from tend.runtime.shared import _common

# Every run status the Actions API accepts that is not `completed`. The API
# takes one status per request and answers an unknown one with an empty list
# rather than an error, so this list is pinned by tests instead of by a 400.
ACTIVE_STATUSES = ("queued", "in_progress", "waiting", "requested", "pending")

# Dedicated workflows only, by workflow file: a run's `name` is its `run-name`
# where the workflow sets one. The poll itself is `tend-notifications`,
# excluded below by run id rather than by workflow, since a second poll would
# own nothing.
WORKFLOW_PREFIX = ".github/workflows/tend-"

# tend-land starts from a CI completion, so no run-level title names a PR: each
# of its sessions is a job named for the PR it works on (`land #7`).
TRIAGE_WORKFLOW = ".github/workflows/tend-triage.yaml"
SUBJECT_JOBS_WORKFLOWS = {".github/workflows/tend-land.yaml", TRIAGE_WORKFLOW}
SUBJECT_JOB_RE = re.compile(r"#(\d+)$")

# A run whose state has not moved in this long is never going to deliver.
# GitHub documents no queue-time cancellation for its hosted runners — the 24h
# one covers self-hosted — so this bound rests on observed state rather than a
# guarantee: a live run advances `updated_at` at every job transition, and the
# shape that could outlast it is a deep `queue: max` backlog. Runs do get
# stranded non-terminal with no jobs and no further updates, and without this
# bound one of them pins its subject for good.
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


def subject_runs(
    runs: list[dict[str, Any]],
    *,
    subject_title: str,
    own_run_id: int,
    now: datetime,
    job_subjects: dict[int, set[int]] | None = None,
    subject_number: int = 0,
) -> list[dict[str, Any]]:
    """Reduce workflow runs to active dedicated candidates for a subject.

    Matches on `display_title` because `workflow_run` does not expose the issue
    number for comment and review events. A run in *job_subjects* (its active
    jobs' subject numbers, for workflows whose jobs name their subject) matches
    on *subject_number* instead.
    """
    job_subjects = job_subjects or {}
    candidates: dict[int, dict[str, Any]] = {}
    for run in runs:
        run_id = int(run["id"])
        if run_id == own_run_id:
            continue
        path = str(run.get("path") or "")
        if not path.startswith(WORKFLOW_PREFIX):
            continue
        if run_id in job_subjects:
            if subject_number not in job_subjects[run_id]:
                continue
        elif run.get("display_title") != subject_title:
            continue
        if path != TRIAGE_WORKFLOW and abandoned(run, now=now):
            continue
        candidates.setdefault(
            run_id,
            {
                "id": run_id,
                "name": PurePosixPath(path).stem,
                "path": path,
                "status": run.get("status"),
                "url": run.get("html_url"),
            },
        )
    return sorted(candidates.values(), key=lambda run: run["id"])


def fetch_active_runs(repo: str) -> list[dict[str, Any]]:
    """Fetch every page per non-terminal status, including long-queued batches."""
    runs: dict[int, dict[str, Any]] = {}
    for status in ACTIVE_STATUSES:
        for page in _common.gh_pages(
            f"repos/{repo}/actions/runs?status={status}&per_page=100"
        ):
            for run in page["workflow_runs"]:
                runs.setdefault(int(run["id"]), run)
    return list(runs.values())


def fetch_job_subjects(repo: str, runs: list[dict[str, Any]]) -> dict[int, set[int]]:
    """Read batch IDs before expansion, then release completed matrix subjects.

    GitHub's runs API does not expose dispatch inputs. Triage's generated
    run-name is therefore an explicit wire format, `triage [1,2]`, retained
    while the workflow waits for its lane and has no jobs. Job names carry
    the same numeric identity after expansion; issue titles are never keys.
    A queued batch can legitimately wait days, so it has no age-based expiry.
    """
    subjects: dict[int, set[int]] = {}
    for run in runs:
        if run.get("path") not in SUBJECT_JOBS_WORKFLOWS:
            continue
        selected: set[int] = set()
        if run["path"] == TRIAGE_WORKFLOW:
            title = str(run["display_title"])
            if not title.startswith("triage "):
                continue
            try:
                numbers = json.loads(title.removeprefix("triage "))
            except json.JSONDecodeError:
                # Invalid dispatches fail validation before creating workers.
                subjects[int(run["id"])] = set()
                continue
            if not isinstance(numbers, list) or any(
                type(number) is not int or number < 1 for number in numbers
            ):
                subjects[int(run["id"])] = set()
                continue
            selected.update(numbers)
        for page in _common.gh_pages(
            f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100"
        ):
            for job in page["jobs"]:
                if match := SUBJECT_JOB_RE.search(str(job.get("name") or "")):
                    number = int(match.group(1))
                    if job["status"] == "completed":
                        selected.discard(number)
                    else:
                        selected.add(number)
        subjects[int(run["id"])] = selected
    return subjects


def main(subject_url: str) -> int:
    repo = _common.repository()
    subject = _common.gh_json("api", subject_url)
    runs = fetch_active_runs(repo)
    _common.dump(
        subject_runs(
            runs,
            subject_title=subject["title"],
            own_run_id=int(os.environ.get("GITHUB_RUN_ID") or 0),
            now=datetime.now(UTC),
            job_subjects=fetch_job_subjects(repo, runs),
            subject_number=int(subject.get("number") or 0),
        )
    )
    return 0
