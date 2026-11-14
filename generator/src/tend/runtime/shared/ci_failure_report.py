"""File, update or close the CI failure report a finished watched run calls for.

`tend-ci-failures` runs this, with no agent, when a watched workflow finishes
on a watched branch of this repository with a verdict. A report is one issue
per workflow, branch and triggering event, the unit a run's verdict speaks
for: a scheduled suite can fail on tests that push runs never reach.

- A failed run comments on the open report. With none open, it reopens the
  lane's newest report unless the lane has passed since that report was
  filed: triage closed it once it had diagnosed its failures, and the lane
  is still in the same red spell. After a pass, or with no report yet, it
  files a new one.
- A passing run closes the open report. Linked repair issues and PRs keep
  their own lifecycle: a pass does not show that a flaky cause is gone, and
  a fix may still be in flight.
- A run that a newer finished run of the same lane supersedes changes nothing.
  Runs can finish out of order, and the newer verdict is the lane's state.

So a report is open while it holds unhandled work, as any open issue does.
The bot's token writes the report, so filing or reopening it triggers ordinary
issue triage. A failure while it is open only adds evidence: a running session
reads it, and when none is running, because a session failed or left a
failure without an owner, the daily `review-runs` sweep revisits the report.
The bot gets no notification for its own writes.

Inputs (env): ``GH_TOKEN`` (the bot's token), ``GITHUB_REPOSITORY``,
``GITHUB_EVENT_PATH`` (a `workflow_run` payload).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from tend.runtime.shared import _common

LABEL = "tend-ci-failure"
LABEL_DESCRIPTION = "A watched CI workflow is failing; Tend tracks its causes here"
LABEL_COLOR = "b60205"
# The verdicts that say something about the lane. The generated job's `if:`
# admits only these, so a cancelled run never takes the lane's pending slot
# from a verdict; everything but `success` reaching here is a failure.
VERDICTS = ("success", "failure", "timed_out", "startup_failure")
# How far back the reporter reads the lane's runs. A lane finishing more runs
# than this while this one waited for its job is a lane whose newest run reports
# itself, and a lane that passed since its newest report was filed shows a pass
# well within this many runs, since a pass's report closes and a later failure
# files a new one.
RECENT_RUNS = 30


def api(path: str, *, method: str = "GET", body: dict | None = None) -> Any:
    """One `gh api` call; a failure raises, and fails the job with gh's error."""
    args = ["api", "--method", method, path]
    if body is not None:
        args += ["--input", "-"]
    result = _common.gh(*args, input=json.dumps(body) if body is not None else None)
    return json.loads(result) if result.strip() else None


def paginated(path: str) -> list[dict[str, Any]]:
    """Every item of a listing, page by page."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = api(f"{path}&page={page}")
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def title(run: dict[str, Any]) -> str:
    return f"`{run['name']}` failing on `{run['head_branch']}` ({run['event']})"


def run_link(run: dict[str, Any]) -> str:
    return f"[run {run['id']}]({run['html_url']}) on `{run['head_sha'][:12]}`"


def lane_verdicts(repo: str, run: dict[str, Any]) -> list[dict[str, Any]]:
    """The lane's recent finished runs that have a verdict, this one included."""
    query = urlencode(
        {
            "branch": run["head_branch"],
            "event": run["event"],
            "status": "completed",
            "per_page": RECENT_RUNS,
        }
    )
    listing = api(f"repos/{repo}/actions/workflows/{run['workflow_id']}/runs?{query}")
    return [
        other
        for other in listing["workflow_runs"]
        if other.get("conclusion") in VERDICTS
    ]


def reports(repo: str, run: dict[str, Any], state: str) -> list[dict[str, Any]]:
    """The lane's reports in `state`, lowest number first.

    A report is an issue with the label and the lane's title. The label assigns
    report ownership independently of who created the issue.
    """
    issues = paginated(f"repos/{repo}/issues?labels={LABEL}&state={state}&per_page=100")
    return sorted(
        (
            issue
            for issue in issues
            if "pull_request" not in issue and issue["title"] == title(run)
        ),
        key=lambda issue: issue["number"],
    )


def comment(repo: str, number: int, body: str) -> None:
    api(f"repos/{repo}/issues/{number}/comments", method="POST", body={"body": body})


def file_report(repo: str, run: dict[str, Any]) -> None:
    # Fails once the label exists, which is every run after the first.
    try:
        api(
            f"repos/{repo}/labels",
            method="POST",
            body={
                "name": LABEL,
                "description": LABEL_DESCRIPTION,
                "color": LABEL_COLOR,
            },
        )
    except subprocess.CalledProcessError:
        pass
    body = (
        f"`{run['name']}` failed on `{run['head_branch']}`: {run_link(run)}, "
        f"triggered by `{run['event']}`.\n\n"
        f"This issue tracks `{run['name']}`'s `{run['event']}` runs on "
        f"`{run['head_branch']}` until one passes. Tend triages it whenever it "
        "opens or reopens: it fixes causes directly or delegates them to separate "
        "issues, links each repair issue or PR here, and closes this issue once the "
        "failures are diagnosed. The next failed run reopens it with a comment, "
        "and a failed run after a pass files a new report.\n"
    )
    api(
        f"repos/{repo}/issues",
        method="POST",
        body={"title": title(run), "body": body, "labels": [LABEL]},
    )


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    run = event["workflow_run"]
    verdicts = lane_verdicts(repo, run)
    if any(other["id"] > run["id"] for other in verdicts):
        print(f"A newer run of this lane has finished; run {run['id']} changes nothing")
        return 0
    open_reports = reports(repo, run, "open")
    if run["conclusion"] == "success":
        if open_reports:
            number = open_reports[0]["number"]
            comment(repo, number, f"{run_link(run)} passed. Closing.")
            api(
                f"repos/{repo}/issues/{number}",
                method="PATCH",
                body={"state": "closed", "state_reason": "completed"},
            )
        return 0
    if open_reports:
        comment(repo, open_reports[0]["number"], f"Failed again: {run_link(run)}.")
        return 0
    closed = reports(repo, run, "closed")
    # GitHub's timestamps are UTC ISO 8601, so they compare as strings.
    if not closed or any(
        other["conclusion"] == "success"
        and other["updated_at"] > closed[-1]["created_at"]
        for other in verdicts
    ):
        file_report(repo, run)
        return 0
    number = closed[-1]["number"]
    # Comment first: reopening starts the triage session that reads it.
    comment(repo, number, f"Failed again: {run_link(run)}.")
    api(f"repos/{repo}/issues/{number}", method="PATCH", body={"state": "open"})
    return 0
