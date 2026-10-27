"""Tests for ci_failure_report.py — tend-ci-failures' report writer.

Every case is an outward write a maintainer sees: a report filed, a comment
posted, an issue closed. The script is inlined into its
workflow, so these load it from the templates directory and swap its one
GitHub call, `api`, for an in-memory repository.
"""

from __future__ import annotations

import importlib.resources
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

sys.path.insert(0, str(importlib.resources.files("tend") / "templates"))
import ci_failure_report as report

REPO = "owner/repo"
BOT = "test-bot"


class FakeGitHub:
    """The endpoints the script reads and writes, over issues and runs in memory."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self.comments: list[tuple[int, str]] = []

    def issue(self, title: str, *, author: str, labels=(), state="open") -> int:
        number = len(self.issues) + 1
        self.issues[number] = {
            "number": number,
            "title": title,
            "state": state,
            "user": {"login": author},
            "labels": [{"name": name} for name in labels],
        }
        return number

    def __call__(self, path: str, *, method: str = "GET", body=None) -> Any:
        if method == "GET" and (
            m := re.fullmatch(rf"repos/{REPO}/actions/workflows/7/runs\?(.*)", path)
        ):
            query = {key: values[0] for key, values in parse_qs(m.group(1)).items()}
            assert query["status"] == "completed"
            runs = [
                run
                for run in self.runs
                if run["head_branch"] == query["branch"]
                and run["event"] == query["event"]
            ]
            return {"workflow_runs": sorted(runs, key=lambda r: -r["id"])}
        if method == "GET" and (
            m := re.fullmatch(
                rf"repos/{REPO}/issues\?labels=([^&]+)&state=open&per_page=100&page=(\d+)",
                path,
            )
        ):
            if m.group(2) != "1":
                return []
            return [
                issue
                for issue in self.issues.values()
                if issue["state"] == "open"
                and any(label["name"] == m.group(1) for label in issue["labels"])
            ]
        if method == "POST" and path == f"repos/{REPO}/labels":
            return {}
        if method == "POST" and path == f"repos/{REPO}/issues":
            number = self.issue(body["title"], author=BOT, labels=body["labels"])
            self.issues[number]["body"] = body["body"]
            return {"number": number}
        if method == "POST" and (
            m := re.fullmatch(rf"repos/{REPO}/issues/(\d+)/comments", path)
        ):
            self.comments.append((int(m.group(1)), body["body"]))
            return {}
        if method == "PATCH" and (
            m := re.fullmatch(rf"repos/{REPO}/issues/(\d+)", path)
        ):
            assert body == {"state": "closed", "state_reason": "completed"}
            self.issues[int(m.group(1))]["state"] = "closed"
            return {}
        raise AssertionError(f"unexpected call: {method} {path}")


def _run(
    run_id: int, conclusion: str, *, event: str = "push", branch: str = "main"
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": "ci",
        "workflow_id": 7,
        "event": event,
        "head_branch": branch,
        "head_sha": f"{run_id:040d}",
        "conclusion": conclusion,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


@pytest.fixture
def github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(report, "api", fake)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "event.json"))
    return fake


def _finish(github: FakeGitHub, run: dict[str, Any]) -> None:
    """The run finishes, and its `workflow_run` event reaches the script."""
    github.runs.append(run)
    Path(report.os.environ["GITHUB_EVENT_PATH"]).write_text(
        json.dumps({"workflow_run": run, "repository": {"default_branch": "main"}})
    )
    assert report.main() == 0


def _open_reports(github: FakeGitHub) -> list[dict[str, Any]]:
    return [
        issue
        for issue in github.issues.values()
        if issue["state"] == "open"
        and "pull_request" not in issue
        and any(label["name"] == report.LABEL for label in issue["labels"])
    ]


def test_a_failure_files_one_report_and_later_failures_comment_on_it(
    github: FakeGitHub,
) -> None:
    _finish(github, _run(100, "failure"))
    _finish(github, _run(101, "timed_out"))

    [filed] = _open_reports(github)
    assert filed["title"] == "`ci` failing on `main` (push)"
    assert [label["name"] for label in filed["labels"]] == [report.LABEL]
    assert "actions/runs/100" in filed["body"]
    assert [(n, "actions/runs/101" in body) for n, body in github.comments] == [
        (filed["number"], True)
    ]


def test_each_event_is_its_own_lane(github: FakeGitHub) -> None:
    """A scheduled suite's failure is not the push suite's: each has a report."""
    _finish(github, _run(100, "failure"))
    _finish(github, _run(101, "failure", event="schedule"))

    assert sorted(issue["title"] for issue in _open_reports(github)) == [
        "`ci` failing on `main` (push)",
        "`ci` failing on `main` (schedule)",
    ]
    assert github.comments == []


def test_a_pass_closes_the_report_and_leaves_its_causes(github: FakeGitHub) -> None:
    """A pass shows the lane green at one commit, not that a flaky cause is
    gone or that a cause's fix in flight is moot, so causes close themselves."""
    _finish(github, _run(100, "failure"))
    [filed] = _open_reports(github)
    cause = github.issue("test_merge: stale fixture", author=BOT)

    _finish(github, _run(101, "success"))

    assert github.issues[filed["number"]]["state"] == "closed"
    assert github.issues[cause]["state"] == "open"
    [(number, body)] = github.comments
    assert number == filed["number"]
    assert "actions/runs/101" in body


def test_a_pass_with_no_open_report_writes_nothing(github: FakeGitHub) -> None:
    _finish(github, _run(100, "success"))

    assert github.issues == {}
    assert github.comments == []


def test_a_superseded_verdict_changes_nothing(github: FakeGitHub) -> None:
    """Runs finish out of order; the newer finished verdict is the lane's state.

    An older green arriving after a newer red must not close the red's report,
    and an older red arriving after a newer green must not reopen one. A newer
    run that was cancelled has no verdict, so it supersedes nothing.
    """
    _finish(github, _run(102, "failure"))
    _finish(github, _run(101, "success"))
    [filed] = _open_reports(github)
    assert github.issues[filed["number"]]["state"] == "open"

    _finish(github, _run(104, "success"))
    _finish(github, _run(103, "failure"))
    assert _open_reports(github) == []

    github.runs.append(_run(106, "cancelled"))
    _finish(github, _run(105, "failure"))
    assert len(_open_reports(github)) == 1


def test_another_lanes_newer_run_supersedes_nothing(github: FakeGitHub) -> None:
    """Newer is judged within the lane: another branch's or event's later
    verdict says nothing about this one."""
    github.runs.append(_run(101, "success", event="schedule"))
    github.runs.append(_run(102, "success", branch="release"))
    _finish(github, _run(100, "failure"))

    assert len(_open_reports(github)) == 1


def test_a_closed_report_is_never_reused(github: FakeGitHub) -> None:
    """A failure after the lane passed is a new incident with a new report."""
    _finish(github, _run(100, "failure"))
    _finish(github, _run(101, "success"))
    _finish(github, _run(102, "failure"))

    [filed] = _open_reports(github)
    assert "actions/runs/102" in filed["body"]
    assert len(github.issues) == 2


@pytest.mark.parametrize("author", [BOT, "github-actions[bot]", "maintainer"])
def test_an_existing_report_is_reused_regardless_of_author(
    github: FakeGitHub, author: str
) -> None:
    existing = github.issue(
        "`ci` failing on `main` (push)", author=author, labels=[report.LABEL]
    )

    _finish(github, _run(100, "failure"))
    _finish(github, _run(101, "success"))

    assert len(github.issues) == 1
    assert github.issues[existing]["state"] == "closed"
    assert [number for number, _ in github.comments] == [existing, existing]


@pytest.mark.parametrize("pull_request", [False, True])
def test_unlabelled_issues_and_pull_requests_are_not_reports(
    github: FakeGitHub, pull_request: bool
) -> None:
    """The label identifies a report, but a PR can never be one."""
    lookalike = github.issue(
        "`ci` failing on `main` (push)",
        author=BOT,
        labels=[report.LABEL] if pull_request else [],
    )
    if pull_request:
        github.issues[lookalike]["pull_request"] = {}

    _finish(github, _run(100, "failure"))

    assert len(_open_reports(github)) == 1
    assert len(github.issues) == 2
    assert github.comments == []
