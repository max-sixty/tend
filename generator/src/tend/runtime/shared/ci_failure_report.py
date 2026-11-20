"""File, update or close the CI failure report a finished watched run calls for.

`tend-ci-failures` runs this, with no agent, when a watched workflow finishes
on a watched branch of this repository with a verdict. A lane is a workflow,
branch and triggering event, the unit a run's verdict speaks for: a scheduled
suite can fail on tests that push runs never reach. A report is an issue with
the label and the lane's title.

- A failed run comments on the lane's open report, or files a new one when
  none is open, so the reporter keeps at most one report open per lane.
- A passing run closes the open report. Linked repair issues and PRs keep
  their own lifecycle: a pass does not show that a flaky cause is gone, and
  a fix may still be in flight.
- A run that a newer finished run of the same lane supersedes changes nothing.
  Runs can finish out of order, and the newer verdict is the lane's state.

So a report is open while it holds unhandled work, as any open issue does.
Triage closes it once it has diagnosed its failures and each has an owner, and
on a lane that stays red the next failure files a new report. Tend never
reopens a closed report. Reopening the lane's last report for each failure
would make one issue hold a whole red spell, however long it lasts: its thread
would grow with each failure, and each triage session would read all of it. The
lane's history lives in its earlier reports and the cause issues their
diagnoses link, which triage searches.

The bot's token writes the report, so filing it triggers ordinary issue triage.
A failure while it is open only adds evidence: a running session reads it, and
when none is running, because a session failed or left a failure without an
owner, the nightly sweep revisits the report. The bot gets no notification for
its own writes.

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
# itself.
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


def open_report(repo: str, run: dict[str, Any]) -> dict[str, Any] | None:
    """The lane's open report, the newest where a maintainer has opened several.

    The label assigns report ownership independently of who created the issue.
    """
    issues = paginated(f"repos/{repo}/issues?labels={LABEL}&state=open&per_page=100")
    return max(
        (
            issue
            for issue in issues
            if "pull_request" not in issue and issue["title"] == title(run)
        ),
        key=lambda issue: issue["number"],
        default=None,
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
        "Tend triages this report: it fixes causes directly or delegates them to "
        "separate issues, links each repair issue or PR here, and closes this "
        f"issue once the failures are diagnosed. Until then, each failed "
        f"`{run['event']}` run of `{run['name']}` on `{run['head_branch']}` adds "
        "a comment here, and a passing one closes it. A failed run after it "
        "closes files a new report.\n"
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
    if any(other["id"] > run["id"] for other in lane_verdicts(repo, run)):
        print(f"A newer run of this lane has finished; run {run['id']} changes nothing")
        return 0
    report = open_report(repo, run)
    if run["conclusion"] == "success":
        if report:
            comment(repo, report["number"], f"{run_link(run)} passed. Closing.")
            api(
                f"repos/{repo}/issues/{report['number']}",
                method="PATCH",
                body={"state": "closed", "state_reason": "completed"},
            )
    elif report:
        comment(repo, report["number"], f"Failed again: {run_link(run)}.")
    else:
        file_report(repo, run)
    return 0
