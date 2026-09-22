# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Own the review-runs workflow's persisted records.

Two of them: the monthly below-threshold evidence tracker, and the lookup that
tells the sweep which `tend-outage` trackers still owe it a drain.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import github_cli

LABEL = "review-runs-tracking"
BODY = """Monthly tracking issue for below-threshold findings. Each run appends findings as a comment. Future runs read these to build cumulative evidence.

**Do not close manually** — a new issue is created each month, and prior months are closed automatically.
"""
EVIDENCE_HEADING = re.compile(r"(^|\n)## Run [0-9]")
OUTAGE_LABEL = "tend-outage"
# `report_failure.py`'s title. The label alone also carries durable trackers,
# which hold no run rows and must not be drained or closed here.
OUTAGE_TITLE = "Bot temporarily unavailable"
TEMP_DIR = Path(tempfile.gettempdir())


def _state_path() -> Path:
    return Path(
        os.environ.get("REVIEW_RUNS_STATE", str(TEMP_DIR / "review-runs-state.json"))
    )


def _findings_path() -> Path:
    return Path(os.environ.get("REVIEW_RUNS_FINDINGS", str(TEMP_DIR / "findings.md")))


def _since_path() -> Path:
    """The sweep's window anchor, as `list_recent_runs.py` writes it."""
    return Path(
        os.environ.get("REVIEW_RUNS_SINCE_FILE", str(TEMP_DIR / "review-runs-since"))
    )


def _issue_number(url: str) -> int:
    return int(url.strip().rstrip("/").rsplit("/", 1)[-1])


def _comments(number: int) -> list[dict[str, Any]]:
    response = github_cli.json_call("issue", "view", str(number), "--json", "comments")
    return [
        {
            "author": github_cli.actor_login(comment.get("author")),
            "body": comment.get("body"),
        }
        for comment in response["comments"]
    ]


def prepare(*, now: datetime | None = None) -> int:
    """Find/create this month's tracker, close stale ones, and show history."""
    now = now or datetime.now(UTC)
    month = now.strftime("%Y-%m")
    previous_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    issues = github_cli.json_call(
        "issue",
        "list",
        "--state",
        "all",
        "--label",
        LABEL,
        "--limit",
        "100",
        "--json",
        "number,title,state",
    )
    current = sorted(
        int(issue["number"])
        for issue in issues
        if issue.get("state") == "OPEN" and month in str(issue.get("title") or "")
    )
    if current:
        number = current[0]
    else:
        url = github_cli.run(
            "issue",
            "create",
            "--title",
            f"{LABEL}: {month}",
            "--label",
            LABEL,
            "--body",
            BODY,
        )
        number = _issue_number(url)

    stale = sorted(
        int(issue["number"])
        for issue in issues
        if issue.get("state") == "OPEN" and month not in str(issue.get("title") or "")
    )
    for old in stale:
        github_cli.run(
            "issue",
            "close",
            str(old),
            "--comment",
            f"Superseded by #{number} ({month}).",
        )

    previous = sorted(
        int(issue["number"])
        for issue in issues
        if previous_month in str(issue.get("title") or "")
    )
    state = {"tracking_number": number, "month": month}
    _state_path().write_text(json.dumps(state, indent=2) + "\n")
    github_cli.dump(
        {
            **state,
            "current_comments": _comments(number),
            "previous_comments": _comments(previous[0]) if previous else [],
        }
    )
    return 0


def _rows(number: int) -> list[str]:
    """Return a tracker's run rows: the body holds the first, comments the rest."""
    issue = github_cli.json_call(
        "issue", "view", str(number), "--json", "body,comments"
    )
    return [
        str(issue.get("body") or ""),
        *(str(comment.get("body") or "") for comment in issue["comments"]),
    ]


def outage_trackers() -> int:
    """Report every outage tracker whose rows this sweep still owes a drain.

    Open trackers, plus any closed since the window anchor by someone other
    than this bot. Only the drain closes a tracker, so a close by anyone else
    — a maintainer watching the incident end — leaves live rows behind that no
    live-repository scan can recover: a merged PR whose review died with the
    outage looks exactly like one the maintainer merged unreviewed.
    """
    since = _since_path().read_text().strip()
    if not since:
        print(
            f"{_since_path()} is empty; run list_recent_runs.py first", file=sys.stderr
        )
        return 2
    repo = github_cli.repository()
    bot = str(github_cli.json_call("api", "user")["login"])
    issues = github_cli.json_call(
        "issue",
        "list",
        "--state",
        "all",
        "--label",
        OUTAGE_LABEL,
        "--author",
        "@me",
        "--limit",
        "100",
        "--json",
        "number,title,state,closedAt",
    )
    trackers: list[dict[str, Any]] = []
    for issue in sorted(issues, key=lambda row: int(row["number"])):
        if str(issue.get("title") or "") != OUTAGE_TITLE:
            continue
        number = int(issue["number"])
        state = str(issue.get("state") or "")
        closed_by = ""
        if state != "OPEN":
            # Both stamps are `%Y-%m-%dT%H:%M:%SZ` UTC, so they order as text.
            if str(issue.get("closedAt") or "") < since:
                continue
            # `closed_by` is on the single-issue REST response only; the list
            # endpoint and `gh issue list --json` both omit it.
            closed = github_cli.json_call("api", f"repos/{repo}/issues/{number}")
            closed_by = github_cli.actor_login(closed.get("closed_by"))
            if closed_by == bot:
                continue
        trackers.append(
            {
                "number": number,
                "state": state,
                "closed_by": closed_by,
                "rows": _rows(number),
            }
        )
    github_cli.dump({"since": since, "trackers": trackers})
    return 0


def _post_comment(repo: str, number: int, body: str) -> None:
    github_cli.run(
        "api",
        f"repos/{repo}/issues/{number}/comments",
        "-X",
        "POST",
        "--input",
        "-",
        input=json.dumps({"body": body}),
    )


def append() -> int:
    """Append this run's findings to its current monthly evidence comment."""
    state = json.loads(_state_path().read_text())
    number = int(state["tracking_number"])
    findings = _findings_path().read_text()
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id or run_id not in findings:
        print(
            f"{_findings_path()} does not contain GITHUB_RUN_ID={run_id}; refusing to post",
            file=sys.stderr,
        )
        return 2

    repo = github_cli.repository()
    bot = str(github_cli.json_call("api", "user")["login"])
    comments = github_cli.paginated(
        "api", "--paginate", f"repos/{repo}/issues/{number}/comments?per_page=100"
    )
    existing = [
        comment
        for comment in comments
        if github_cli.actor_login(comment.get("user")) == bot
        and EVIDENCE_HEADING.search(str(comment.get("body") or ""))
    ]
    if not existing:
        _post_comment(repo, number, findings)
        github_cli.dump({"tracking_number": number, "action": "created"})
        return 0

    prior = str(existing[-1].get("body") or "")
    combined = prior + findings
    if len(combined.encode()) >= 60_000:
        _post_comment(repo, number, findings)
        action = "created"
    else:
        github_cli.run(
            "api",
            f"repos/{repo}/issues/comments/{existing[-1]['id']}",
            "-X",
            "PATCH",
            "--input",
            "-",
            input=json.dumps({"body": combined}),
        )
        action = "appended"
    github_cli.dump({"tracking_number": number, "action": action})
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["prepare-evidence"]:
        return prepare()
    if args == ["append-evidence"]:
        return append()
    if args == ["outage-trackers"]:
        return outage_trackers()
    print(
        f"usage: {sys.argv[0]} prepare-evidence|append-evidence|outage-trackers",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from None
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
