# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Validate a selected triage batch after it acquires the workflow lane.

Selection judgment belongs to notifications/recovery. This boundary deduplicates
IDs and removes closed issues, PRs and bookkeeping before expanding real issue
jobs. GitHub limits each matrix to 256 jobs; callers split larger selections.
Open issues remain the recovery backlog after a cancelled or failed run.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def issue_numbers(raw: str) -> list[int]:
    numbers = json.loads(raw)
    if not isinstance(numbers, list) or any(
        type(number) is not int or number < 1 for number in numbers
    ):
        raise ValueError("issues must be a JSON array of positive issue numbers")
    numbers = list(dict.fromkeys(numbers))
    if len(numbers) > 256:
        raise ValueError("a triage batch cannot exceed GitHub's 256-job matrix limit")
    return numbers


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    bookkeeping = set(json.loads(os.environ["BOOKKEEPING_LABELS"]))
    selected = []
    for number in issue_numbers(os.environ["ISSUES"]):
        issue = json.loads(
            subprocess.check_output(
                ["gh", "api", f"repos/{repo}/issues/{number}"], text=True
            )
        )
        if (
            issue["state"] == "open"
            and "pull_request" not in issue
            and not bookkeeping.intersection(label["name"] for label in issue["labels"])
        ):
            selected.append(number)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"issues={json.dumps(selected, separators=(',', ':'))}\n")


if __name__ == "__main__":
    main()
