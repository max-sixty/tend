# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Keep unfinished issue triage durable outside GitHub's bounded run queue.

Enqueue an open issue before its agent job queues. Remove the pending label
only after a successful agent turn; failed or cancelled runs leave it for
recovery. Inputs: GH_TOKEN, GITHUB_REPOSITORY, ISSUE_NUMBER, QUEUE_ACTION
(enqueue, admit or complete), GITHUB_OUTPUT, and optionally GITHUB_API_URL.
Enqueue records work; admit only accepts work still pending. Both output ready
and generation (the latest reopen event ID, or zero). Complete
takes that admitted generation as QUEUE_GENERATION and preserves newer work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LABEL = "tend-triage-pending"


def api(path: str, *, method: str = "GET", body: dict | None = None):
    request = Request(
        f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def ensure_label(repo: str) -> None:
    path = f"repos/{repo}/labels/{LABEL}"
    try:
        api(path)
        return
    except HTTPError as error:
        if error.code != 404:
            raise
    try:
        api(
            f"repos/{repo}/labels",
            method="POST",
            body={
                "name": LABEL,
                "color": "ededed",
                "description": "Tend issue triage has not completed successfully",
            },
        )
    except HTTPError as error:
        if error.code != 422:
            raise
        # Another enqueue may have created the label since our GET.
        api(path)


def generation(path: str) -> int:
    """A reopen creates new work even while an earlier triage is finishing."""
    latest = 0
    page = 1
    while True:
        events = api(f"{path}/events?per_page=100&page={page}")
        latest = max(
            latest,
            max(
                (event["id"] for event in events if event["event"] == "reopened"),
                default=0,
            ),
        )
        if len(events) < 100:
            return latest
        page += 1


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    number = int(os.environ["ISSUE_NUMBER"])
    if number <= 0:
        raise ValueError("ISSUE_NUMBER must be a positive issue number")
    path = f"repos/{repo}/issues/{number}"
    action = os.environ["QUEUE_ACTION"]
    if action == "complete":
        admitted = int(os.environ["QUEUE_GENERATION"])
        if generation(path) != admitted:
            return 0
        try:
            api(f"{path}/labels/{LABEL}", method="DELETE")
        except HTTPError as error:
            if error.code != 404:
                raise
        # GitHub has no conditional label deletion. Restore a reopen that
        # raced the delete; a later reopen's own enqueue adds the label.
        if generation(path) != admitted:
            api(f"{path}/labels", method="POST", body={"labels": [LABEL]})
    elif action in ("enqueue", "admit"):
        issue = api(path)
        if "pull_request" in issue:
            raise ValueError("Issue triage cannot enqueue a pull request")
        pending = any(label["name"] == LABEL for label in issue["labels"])
        ready = issue["state"] == "open" and (action == "enqueue" or pending)
        admitted = generation(path) if ready else 0
        if ready and not pending:
            ensure_label(repo)
            api(f"{path}/labels", method="POST", body={"labels": [LABEL]})
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write(f"ready={str(ready).lower()}\n")
            output.write(f"generation={admitted}\n")
    else:
        raise ValueError(f"Unknown QUEUE_ACTION: {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
