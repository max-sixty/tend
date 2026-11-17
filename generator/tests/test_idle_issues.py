"""Tests for idle_issues.py — the open issues only the nightly and weekly sweeps reach.

The bot gets no event for its own writes, so an issue with no assignee, run or
closing pull request to act next waits for a sweep: nightly restarts every such
CI cause, and weekly reads the other issues the bot had the last word on.
Listing an issue something else will act on sends a second worker after work it
already holds; leaving one out strands it, since no other workflow returns to
it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests import GH_PREAMBLE, fake_bin, runtime_command, tool_path

BOT = "widget-agent"

FAKE_GH = (
    GH_PREAMBLE
    + r"""
case "$*" in
  *"/actions/runs?status="*)
    args="$*"
    status="${args#*status=}"
    status="${status%%&*}"
    file="$RUNS_DIR/$status.json"
    [ -f "$file" ] && emit "$(cat "$file")" || emit '{"workflow_runs": []}'
    ;;
  *"/actions/runs/"*"/jobs"*) emit '{"jobs": []}' ;;
  *" graphql "*) emit "$(cat "$ISSUE_PAGES")" ;;
  "api user") emit '{"login": "widget-agent", "id": 1}' ;;
  *) exit 1 ;;
esac
"""
)


def _issue(
    number: int,
    *,
    labels: tuple = (),
    author: str = BOT,
    comments: tuple = (BOT,),
    assignees: tuple = (),
    closing: int = 0,
    refs: tuple = (),
) -> dict:
    """An open issue; each comment is a login, an app's ``(login, "Bot")``, or None
    for a deleted account."""
    nodes = []
    for minute, who in enumerate(comments):
        login, kind = who if isinstance(who, tuple) else (who, "User")
        nodes.append(
            {
                "author": None if who is None else {"__typename": kind, "login": login},
                "createdAt": f"2026-11-01T08:{minute:02d}:00Z",
            }
        )
    return {
        "number": number,
        "title": f"issue {number}",
        "url": f"https://github.com/owner/repo/issues/{number}",
        "author": {"__typename": "User", "login": author},
        "labels": {"nodes": [{"name": label} for label in labels]},
        "assignees": {"nodes": [{"login": login} for login in assignees]},
        "comments": {"nodes": nodes},
        "closedByPullRequestsReferences": {"totalCount": closing},
        "timelineItems": {
            "nodes": [{"source": {"number": pr, "state": state}} for pr, state in refs]
        },
    }


def _page(issues: list[dict], more: bool) -> dict:
    return {
        "data": {
            "repository": {
                "issues": {
                    "pageInfo": {"hasNextPage": more, "endCursor": "c"},
                    "nodes": issues,
                }
            }
        }
    }


CAUSE = ("tend-ci-cause",)


def test_lists_causes_and_the_issues_the_bot_last_wrote_on_that_nothing_will_act_on(
    tmp_path: Path,
) -> None:
    pages = [
        _page(
            [
                _issue(1, labels=CAUSE, comments=(BOT, "maintainer")),
                _issue(2, labels=CAUSE, assignees=("maintainer",)),
                _issue(3, labels=CAUSE, closing=1),
                _issue(4, labels=CAUSE),
                _issue(5, labels=CAUSE, assignees=(BOT,)),
            ],
            more=True,
        ),
        _page(
            [
                _issue(6),
                _issue(7, comments=(BOT, "maintainer")),
                _issue(8, assignees=("maintainer",)),
                _issue(9, closing=1),
                _issue(10),
                _issue(11, refs=((50, "OPEN"), (51, "MERGED"))),
                _issue(12, comments=()),
                _issue(13, author="maintainer", comments=()),
                _issue(14, assignees=(BOT,)),
                _issue(15, comments=(BOT, ("github-actions", "Bot"))),
                _issue(16, comments=(BOT, None)),
                _issue(17, labels=("tend-ci-failure",)),
                _issue(18, labels=("tend-outage",)),
                _issue(19, labels=("tend-rate-limit",)),
                _issue(20, labels=("review-runs-tracking",)),
                _issue(21, labels=("review-reviewers-tracking",)),
            ],
            more=False,
        ),
    ]
    issue_pages = tmp_path / "issues.json"
    issue_pages.write_text("".join(json.dumps(page) for page in pages))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "queued.json").write_text(
        json.dumps(
            {
                "workflow_runs": [
                    {
                        "id": 70,
                        "path": ".github/workflows/tend-triage.yaml",
                        "status": "queued",
                        "display_title": "triage [4,10]",
                    }
                ]
            }
        )
    )
    env = {
        "PATH": tool_path(fake_bin(tmp_path, gh=FAKE_GH)),
        "GH_CALLS": str(tmp_path / "gh-calls.log"),
        "GITHUB_REPOSITORY": "owner/repo",
        "ISSUE_PAGES": str(issue_pages),
        "RUNS_DIR": str(runs_dir),
    }

    result = subprocess.run(
        runtime_command("idle-issues"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    def cause(number: int) -> dict:
        return {
            "number": number,
            "title": f"issue {number}",
            "url": f"https://github.com/owner/repo/issues/{number}",
        }

    def other(number: int, last_comment_at: str | None, open_prs=()) -> dict:
        return {
            **cause(number),
            "labels": [],
            "last_comment_at": last_comment_at,
            "open_prs": list(open_prs),
        }

    assert json.loads(result.stdout) == {
        "open": 21,
        "causes": [cause(1), cause(5)],
        "others": [
            other(6, "2026-11-01T08:00:00Z"),
            other(11, "2026-11-01T08:00:00Z", [50]),
            other(12, None),
            other(14, "2026-11-01T08:00:00Z"),
            other(15, "2026-11-01T08:00:00Z"),
        ],
    }
