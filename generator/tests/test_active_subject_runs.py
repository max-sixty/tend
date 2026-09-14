"""Tests for active_subject_runs.py — which dedicated run owns a subject.

The notifications poll defers a thread whose subject a dedicated `tend-*`
workflow is already handling. Reading that wrong costs an outward action: the
poll answers a maintainer the queued `tend-mention` run was booted to answer,
and both sessions post as the same bot account.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests import GH_PREAMBLE, fake_bin, tool_path, uv_script

ACTIVE_SUBJECT_RUNS = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "tend-ci-runner"
    / "scripts"
    / "active_subject_runs.py"
)
SUBJECT_URL = "https://api.github.com/repos/owner/repo/pulls/4082"
TITLE = "fix: keep the resolved workspace root across retries"
OWN_RUN_ID = 999

# One runs file per status, so a fake `gh` answers `status=queued` with the
# queued runs and nothing else — exactly the partition the real API applies,
# and the one an `in_progress`-only query reads half of.
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
  *"/pulls/"*) emit "$(cat "$SUBJECT_JSON")" ;;
  *) exit 1 ;;
esac
"""
)


def _run(rid: int, name: str, status: str, title: str = TITLE) -> dict:
    return {
        "id": rid,
        "name": name,
        "status": status,
        "display_title": title,
        "html_url": f"https://github.com/owner/repo/actions/runs/{rid}",
    }


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """Fake `gh` over a repository with no active runs at all."""
    bindir = fake_bin(tmp_path, gh=FAKE_GH)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    subject = tmp_path / "subject.json"
    subject.write_text(json.dumps({"title": TITLE}))
    return {
        "PATH": tool_path(bindir),
        "GH_CALLS": str(tmp_path / "gh-calls.log"),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": str(OWN_RUN_ID),
        "RUNS_DIR": str(runs_dir),
        "SUBJECT_JSON": str(subject),
    }


def _stage(env: dict[str, str], status: str, *runs: dict) -> None:
    path = Path(env["RUNS_DIR"]) / f"{status}.json"
    path.write_text(json.dumps({"workflow_runs": list(runs)}))


def _owners(env: dict[str, str]) -> list[dict]:
    result = subprocess.run(
        uv_script(ACTIVE_SUBJECT_RUNS, SUBJECT_URL),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_queued_run_owns_the_subject(env: dict[str, str]) -> None:
    """The regression: a created-but-unstarted run still owns its subject.

    `queued` is a status of its own in the Actions API, so a dedicated run that
    has not been picked up yet answers no `status=in_progress` query. Reading
    that as unowned is what let the poll act on a subject `tend-mention` held.
    """
    _stage(env, "queued", _run(101, "tend-mention", "queued"))
    assert [run["id"] for run in _owners(env)] == [101]


def test_every_non_terminal_status_is_queried(env: dict[str, str]) -> None:
    """Each non-terminal status needs its own request — one `status=` per call.

    The API answers an unrecognised status with an empty list rather than an
    error, so nothing but this assertion catches a mistyped or dropped one.
    """
    _owners(env)
    calls = Path(env["GH_CALLS"]).read_text()
    for status in ("queued", "in_progress", "waiting", "requested", "pending"):
        assert f"status={status}&" in calls


def test_deployment_gated_run_owns_the_subject(env: dict[str, str]) -> None:
    """A run held at an environment gate owns its subject like a running one."""
    _stage(env, "waiting", _run(102, "tend-review", "waiting"))
    assert [run["id"] for run in _owners(env)] == [102]


def test_in_progress_run_still_owns_the_subject(env: dict[str, str]) -> None:
    _stage(env, "in_progress", _run(103, "tend-review", "in_progress"))
    assert _owners(env) == [
        {
            "id": 103,
            "name": "tend-review",
            "status": "in_progress",
            "url": "https://github.com/owner/repo/actions/runs/103",
        }
    ]


def test_own_run_and_other_subjects_are_not_owners(env: dict[str, str]) -> None:
    """The poll does not defer to itself, to a non-Tend workflow, or to a run
    working some other subject."""
    _stage(
        env,
        "in_progress",
        _run(OWN_RUN_ID, "tend-notifications", "in_progress"),
        _run(104, "ci", "in_progress"),
        _run(105, "tend-review", "in_progress", title="some other pull request"),
    )
    assert _owners(env) == []


def test_a_run_seen_twice_is_reported_once(env: dict[str, str]) -> None:
    """Status queries are separate requests, so a run that starts between two
    of them answers both. It is one owner, not two."""
    _stage(env, "queued", _run(106, "tend-mention", "queued"))
    _stage(env, "in_progress", _run(106, "tend-mention", "in_progress"))
    owners = _owners(env)
    assert [run["id"] for run in owners] == [106]
    assert owners[0]["status"] == "queued"
