"""Tests for idle_issues.py — the open issues only the nightly sweep will reach.

The bot gets no event for its own writes, so an issue it last wrote on, with no
assignee, run or closing pull request to act next, waits for the nightly sweep.
Listing one the sweep shouldn't own sends a second worker after work something
already holds; leaving one out strands it, since no other workflow returns to it.
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
        "labels": {"nodes": [{"name": "tend-ci-cause"}]},
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


def test_lists_issues_the_bot_last_wrote_on_that_nothing_will_act_on(
    tmp_path: Path,
) -> None:
    pages = [
        _page(
            [
                _issue(1),
                _issue(2, comments=(BOT, "maintainer")),
                _issue(3, assignees=("maintainer",)),
                _issue(4, closing=1),
                _issue(5, refs=((50, "OPEN"), (51, "MERGED"))),
                _issue(6, comments=()),
            ],
            more=True,
        ),
        _page(
            [
                _issue(7),
                _issue(8, author="maintainer", comments=()),
                _issue(9, assignees=(BOT,)),
                _issue(10, comments=(BOT, ("github-actions", "Bot"))),
                _issue(11, comments=(BOT, None)),
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
                        "display_title": "triage [7]",
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

    def listed(number: int, last_comment_at: str | None, open_prs=()) -> dict:
        return {
            "number": number,
            "title": f"issue {number}",
            "url": f"https://github.com/owner/repo/issues/{number}",
            "labels": ["tend-ci-cause"],
            "last_comment_at": last_comment_at,
            "open_prs": list(open_prs),
        }

    assert json.loads(result.stdout) == {
        "open": 11,
        "issues": [
            listed(1, "2026-11-01T08:00:00Z"),
            listed(5, "2026-11-01T08:00:00Z", [50]),
            listed(6, None),
            listed(9, "2026-11-01T08:00:00Z"),
            listed(10, "2026-11-01T08:00:00Z"),
        ],
    }
