"""Rerun one workflow run's failed jobs and report the new conclusions."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from tend.runtime.shared import _common


def _run_attempt(path: str) -> int:
    return int(_common.gh_json("api", path)["run_attempt"])


def _job(path: str, job_id: int) -> dict[str, Any]:
    return _common.gh_json("api", f"{path.rsplit('/runs/', 1)[0]}/jobs/{job_id}")


def main(run_id: str, *, sleep: Callable[[float], None] = time.sleep) -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    run_api = f"repos/{repo}/actions/runs/{run_id}"
    base_attempt = _run_attempt(run_api)
    _common.gh("run", "rerun", run_id, "--failed", "--repo", repo)

    attempt = base_attempt
    for _ in range(6):
        sleep(5)
        attempt = _run_attempt(run_api)
        if attempt > base_attempt:
            break
    if attempt <= base_attempt:
        print(
            f"no new attempt surfaced after the rerun (still attempt {attempt}) "
            "— the rerun did not take; UNVERIFIED"
        )
        return 1

    # `filter=latest` lists every job in the run, the ones the rerun left alone
    # too, so on a wide matrix a re-run job can sit past the first page.
    pages = _common.gh_pages(f"{run_api}/jobs?filter=latest&per_page=100")
    job_ids = [
        int(job["id"])
        for page in pages
        for job in page["jobs"]
        if int(job["run_attempt"]) == attempt
    ]
    if not job_ids:
        print(f"attempt {attempt} exists but lists no jobs yet — UNVERIFIED")
        return 1

    # No time bound: the jobs end by their own timeouts, or the session's ends
    # the wait.
    while True:
        jobs = [_job(run_api, job_id) for job_id in job_ids]
        if all(job["status"] == "completed" for job in jobs):
            for job in jobs:
                print(f"{job.get('conclusion') or ''}\t{job['name']}")
            return 0
        sleep(60)
