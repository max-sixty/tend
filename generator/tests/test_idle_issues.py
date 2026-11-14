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


def _issue(number: int, *, author: str = BOT, last: str | None = BOT, **extra) -> dict:
    comments = [
        {"author": {"login": last}, "createdAt": f"2026-11-0{number}T08:00:00Z"}
    ]
    return {
        "number": number,
        "title": f"issue {number}",
        "url": f"https://github.com/owner/repo/issues/{number}",
        "author": {"login": author},
        "labels": {"nodes": [{"name": "tend-ci-cause"}]},
        "assignees": {"totalCount": extra.get("assignees", 0)},
        "comments": {"nodes": comments if last else []},
        "timelineItems": {"nodes": extra.get("timeline", [])},
    }


def _ref(pr: int, state: str, closes: bool) -> dict:
    return {
        "__typename": "CrossReferencedEvent",
        "willCloseTarget": closes,
        "source": {"number": pr, "state": state},
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
                _issue(2, last="maintainer"),
                _issue(3, assignees=1),
                _issue(4, timeline=[_ref(40, "OPEN", True)]),
            ],
            more=True,
        ),
        _page(
            [
                _issue(5, timeline=[_ref(50, "OPEN", False), _ref(51, "MERGED", True)]),
                _issue(6, last=None),
                _issue(7),
                _issue(8, author="maintainer", last=None),
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
    assert json.loads(result.stdout) == {
        "open": 8,
        "issues": [
            {
                "number": 1,
                "title": "issue 1",
                "url": "https://github.com/owner/repo/issues/1",
                "labels": ["tend-ci-cause"],
                "last_comment_at": "2026-11-01T08:00:00Z",
                "open_prs": [],
            },
            {
                "number": 5,
                "title": "issue 5",
                "url": "https://github.com/owner/repo/issues/5",
                "labels": ["tend-ci-cause"],
                "last_comment_at": "2026-11-05T08:00:00Z",
                "open_prs": [50],
            },
            {
                "number": 6,
                "title": "issue 6",
                "url": "https://github.com/owner/repo/issues/6",
                "labels": ["tend-ci-cause"],
                "last_comment_at": None,
                "open_prs": [],
            },
        ],
    }
