"""Find or create the current UTC month's review-reviewers tracking issue,
and close earlier months' trackers."""

from __future__ import annotations

import datetime
import json
import os
import subprocess

LABEL = "review-reviewers-tracking"
BODY = """Monthly tracking issue for `review-reviewers`. Per-target evidence lives in secret gists owned by the bot. A comment below is posted when each target's gist is first created.

**Do not close manually** — a new issue is created each month, and prior months are closed automatically.
"""


def main() -> None:
    month = datetime.datetime.now(datetime.UTC).strftime("%Y-%m")
    repo = os.environ["GITHUB_REPOSITORY"]
    issues = json.loads(
        subprocess.check_output(
            [
                "gh",
                "issue",
                "list",
                "-R",
                repo,
                "--state",
                "open",
                "--label",
                LABEL,
                "--json",
                "number,title",
            ],
            text=True,
        )
    )
    for stale in sorted(
        issue["number"] for issue in issues if month not in issue["title"]
    ):
        subprocess.run(
            [
                "gh",
                "issue",
                "close",
                str(stale),
                "-R",
                repo,
                "--comment",
                f"Superseded by the {month} tracker.",
            ],
            check=True,
        )
    existing = sorted(issue["number"] for issue in issues if month in issue["title"])
    if existing:
        print(f"Tracking issue for {month} exists: #{existing[0]}")
        return
    subprocess.run(
        [
            "gh",
            "issue",
            "create",
            "-R",
            repo,
            "--title",
            f"{LABEL}: {month}",
            "--label",
            LABEL,
            "-F",
            "-",
        ],
        input=BODY,
        text=True,
        check=True,
    )


if __name__ == "__main__":
    main()
