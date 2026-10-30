"""Tests for shell step bodies and workflow runtime operations.

The composite actions' remaining shell steps under shared/steps/ and the
generator's preflight scripts are all exercised at their runtime boundary.
Shellcheck cannot catch runtime behavior, so shell steps run as commands;
runtime Python runs against a fake `gh` and an injected clock. Shared Python
step bodies live in tend.runtime.shared and are tested in shared/steps/.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tend.runtime.shared import notifications_check

from tests import GH_PREAMBLE, fake_bin, tool_path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _calls(env: dict[str, str]) -> list[str]:
    return Path(env["GH_CALLS"]).read_text().splitlines()


def _comments(env: dict[str, str]) -> str:
    """Every comment body the fake `gh` was handed on stdin, concatenated."""
    return Path(env["COMMENT_BODIES"]).read_text()


def _output(env: dict[str, str], key: str) -> str:
    """The value a pre-check wrote to `$GITHUB_OUTPUT` under *key*.

    Exactly one write: a second would leave the step's consumers reading
    whichever line Actions kept, so two is a defect rather than a last-wins.
    """
    lines = Path(env["GITHUB_OUTPUT"]).read_text().splitlines()
    values = [line.split("=", 1)[1] for line in lines if line.startswith(f"{key}=")]
    assert len(values) == 1, f"expected exactly one {key}, got: {lines}"
    return values[0]


# ---------------------------------------------------------------------------
# notifications_check.py — the tend-notifications pre-check
# ---------------------------------------------------------------------------

# `gh` stand-in for the notifications pre-check. The real notifications call
# uses `--paginate --slurp`, so the fake puts each fixture item on its own page.
# Counting more than one item therefore exercises the page flattening too.
FAKE_GH_NOTIFICATIONS = (
    GH_PREAMBLE
    + r"""
case "$1:$2" in
  api:--paginate)
    [ -z "${FAIL_NOTIFS:-}" ] || exit 1
    # A 200 carrying something other than JSON, verbatim.
    if [ -n "${RAW_BODY:-}" ]; then cat "$RAW_BODY"; exit 0; fi
    # GitHub applies the strict `before` boundary server-side. The fixture uses
    # the pre-check's fixed cutoff so boundary and fresh activity stay unread.
    pages=$(jq -c --arg cutoff "$NOTIF_CUTOFF" \
      '[.[] | select(.updated_at < $cutoff)]' "$NOTIFICATIONS_JSON")
    if [ "$pages" = "[]" ]; then
      emit '[]'
    else
      emit "$(printf '%s\n' "$pages" | jq -c '.[] | [.]')"
    fi
    ;;
  api:repos/*/subscription)
    [ -z "${FAIL_SUBSCRIPTION_WRITE:-}" ] || exit 1
    echo "put" >> "$SUBSCRIPTION_WRITES"
    emit '{"subscribed":true,"ignored":false}'
    ;;
  api:user)
    [ -z "${FAIL_PRS:-}" ] || exit 1
    emit '{"login":"test-bot"}'
    ;;
  api:graphql)
    [ -z "${FAIL_PRS:-}" ] || exit 1
    emit "$(jq -c '{data: {search: {nodes: [.[] | .comments = {nodes: .comments}]}}}' "$PULLS_JSON")"
    ;;
  *) exit 1 ;;
esac
"""
)

# The injected clock puts "now" at 12:00, so the queue snapshot ends at 11:50.
NOTIF_FRESH = "2026-01-02T11:55:00Z"
NOTIF_SETTLED = "2026-01-02T11:45:00Z"
NOTIF_CUTOFF = "2026-01-02T11:50:00Z"


def _notif(
    tid: str, kind: str, number: int, updated_at: str, repo: str = "owner/repo"
) -> dict:
    """One unread notification as `GET /notifications` returns it.

    *kind* is the subject's path segment: `pulls` or `issues`.
    """
    return {
        "id": tid,
        "updated_at": updated_at,
        "repository": {"full_name": repo},
        "subject": {
            "url": f"https://api.github.com/repos/{repo}/{kind}/{number}",
            "type": "PullRequest" if kind == "pulls" else "Issue",
        },
    }


@pytest.fixture
def notifications_env(tmp_path: Path) -> dict[str, str]:
    """Fake gh on PATH, plus the workflow env the pre-check reads."""
    bindir = fake_bin(tmp_path, gh=FAKE_GH_NOTIFICATIONS)

    notifications = tmp_path / "notifications.json"
    notifications.write_text("[]")
    pulls = tmp_path / "pulls.json"
    pulls.write_text("[]")
    subscription_writes = tmp_path / "subscription-writes"
    subscription_writes.write_text("")

    return {
        "PATH": tool_path(bindir),
        "GH_CALLS": str(tmp_path / "gh-calls.log"),
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
        "GITHUB_REPOSITORY": "owner/repo",
        "NOTIFICATIONS_JSON": str(notifications),
        "PULLS_JSON": str(pulls),
        "NOTIF_CUTOFF": NOTIF_CUTOFF,
        "SUBSCRIPTION_WRITES": str(subscription_writes),
    }


def _run_check(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(os, "environ", env.copy())
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = notifications_check.main(
                now=datetime(2026, 1, 2, 12, tzinfo=UTC)
            )
    return subprocess.CompletedProcess(
        ["tend runtime notifications-check"],
        returncode,
        stdout.getvalue(),
        stderr.getvalue(),
    )


def _write_json(env: dict[str, str], key: str, value: object) -> None:
    Path(env[key]).write_text(json.dumps(value))


def test_notifications_check_reports_no_work_on_an_empty_inbox(
    notifications_env: dict[str, str],
) -> None:
    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "0"
    assert _output(notifications_env, "conflict_count") == "0"
    assert _output(notifications_env, "cutoff") == NOTIF_CUTOFF
    assert Path(notifications_env["SUBSCRIPTION_WRITES"]).read_text() == "put\n", (
        result.stdout + result.stderr
    )


def test_notifications_check_counts_a_complete_cutoff_snapshot_without_acknowledging(
    notifications_env: dict[str, str],
) -> None:
    _write_json(
        notifications_env,
        "NOTIFICATIONS_JSON",
        [
            _notif("11", "issues", 7, NOTIF_SETTLED),
            _notif("22", "pulls", 8, NOTIF_SETTLED, repo="other/repo"),
            _notif("33", "issues", 9, NOTIF_CUTOFF),
            _notif("44", "issues", 10, NOTIF_FRESH),
        ],
    )

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "2"
    calls = Path(notifications_env["GH_CALLS"]).read_text()
    assert f"notifications?before={NOTIF_CUTOFF}" in calls
    assert "--paginate" in calls
    assert "notifications/threads/" not in calls


def test_notifications_check_ignores_an_unsettled_mergeable(
    notifications_env: dict[str, str],
) -> None:
    # A lazily-computed `UNKNOWN` is not a conflict: only a settled
    # `CONFLICTING` counts, so a merge into the base branch boots no session.
    _write_json(
        notifications_env,
        "PULLS_JSON",
        [
            {
                "number": 11,
                "mergeable": "MERGEABLE",
                "headRefOid": "head-11",
                "comments": [],
            },
            {
                "number": 22,
                "mergeable": "UNKNOWN",
                "headRefOid": "head-22",
                "comments": [],
            },
            {
                "number": 33,
                "mergeable": "CONFLICTING",
                "headRefOid": "head-33",
                "comments": [],
            },
        ],
    )

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "0"
    assert _output(notifications_env, "conflict_count") == "1"
    assert "1 possible conflicted bot PR(s)" in result.stdout
    calls = Path(notifications_env["GH_CALLS"]).read_text()
    assert "api graphql" in calls
    assert "comments(last: 100)" in calls
    assert "repo:owner/repo author:test-bot is:pr is:open" in calls
    assert "app/dependabot" not in calls
    assert "app/renovate" not in calls


def test_notifications_check_survives_a_colour_forcing_job_environment(
    notifications_env: dict[str, str],
) -> None:
    """A consumer whose workflow env carries `CLICOLOR_FORCE=1` — a `env:`
    override, or a `setup:` step that wrote one into `$GITHUB_ENV` — would
    otherwise get ANSI codes inside every `gh` body. Both readers catch the
    decode error and fall back to zero, so the poll skips every cycle with
    nothing but a `::warning::` on a green job to say so."""
    notifications_env["CLICOLOR_FORCE"] = "1"
    _write_json(
        notifications_env,
        "NOTIFICATIONS_JSON",
        [_notif("11", "issues", 7, NOTIF_SETTLED)],
    )
    _write_json(
        notifications_env,
        "PULLS_JSON",
        [
            {
                "number": 33,
                "mergeable": "CONFLICTING",
                "headRefOid": "head-33",
                "comments": [],
            }
        ],
    )

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "1"
    assert _output(notifications_env, "conflict_count") == "1"


def test_notifications_check_suppresses_only_the_marked_bot_head(
    notifications_env: dict[str, str],
) -> None:
    _write_json(
        notifications_env,
        "PULLS_JSON",
        [
            {
                "number": 22,
                "mergeable": "CONFLICTING",
                "headRefOid": "head-22",
                "comments": [
                    {
                        "author": {"login": "test-bot"},
                        "body": "<!-- tend-conflict-deferred head=head-22 -->",
                    }
                ],
            },
            {
                "number": 33,
                "mergeable": "CONFLICTING",
                "headRefOid": "head-33",
                "comments": [
                    {
                        "author": {"login": "test-bot"},
                        "body": "<!-- tend-conflict-deferred head=old-head -->",
                    }
                ],
            },
            {
                "number": 44,
                "mergeable": "CONFLICTING",
                "headRefOid": "head-44",
                "comments": [
                    {
                        "author": {"login": "someone-else"},
                        "body": "<!-- tend-conflict-deferred head=head-44 -->",
                    }
                ],
            },
            {
                "number": 55,
                "mergeable": "CONFLICTING",
                "headRefOid": "head-55",
                "comments": [
                    {
                        "author": {"login": "test-bot"},
                        "body": (
                            "Quoting <!-- tend-conflict-deferred head=head-55 -->"
                            " is not resolver state."
                        ),
                    }
                ],
            },
        ],
    )

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "conflict_count") == "3"
    calls = Path(notifications_env["GH_CALLS"]).read_text()
    assert "comments(last: 100)" in calls


def test_notifications_check_retries_a_failed_conflict_scan_next_cycle(
    notifications_env: dict[str, str],
) -> None:
    notifications_env["FAIL_PRS"] = "1"

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "conflict_count") == "0"
    assert "bot PR conflict scan failed" in result.stdout


def test_notifications_check_enforces_repository_watching_every_cycle(
    notifications_env: dict[str, str],
) -> None:
    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "0"
    assert Path(notifications_env["SUBSCRIPTION_WRITES"]).read_text() == "put\n"
    calls = Path(notifications_env["GH_CALLS"]).read_text()
    assert "-X PUT -F subscribed=true -F ignored=false" in calls


def test_notifications_check_still_reads_the_queue_when_watching_repair_fails(
    notifications_env: dict[str, str],
) -> None:
    _write_json(
        notifications_env,
        "NOTIFICATIONS_JSON",
        [_notif("999", "issues", 7, NOTIF_SETTLED)],
    )
    notifications_env["FAIL_SUBSCRIPTION_WRITE"] = "1"

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "1"
    assert "could not enable repository watching" in result.stdout


def test_notifications_check_gives_up_cleanly_when_the_fetch_keeps_failing(
    notifications_env: dict[str, str],
) -> None:
    """The step is `bash -e`, so an untolerated `gh` failure would fail the job
    red. A cycle that can't enumerate just skips — the next one picks it up.
    """
    _write_json(
        notifications_env,
        "NOTIFICATIONS_JSON",
        [_notif("999", "issues", 7, NOTIF_SETTLED)],
    )
    notifications_env["FAIL_NOTIFS"] = "1"

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "0"


def test_notifications_check_tolerates_an_html_200(
    notifications_env: dict[str, str], tmp_path: Path
) -> None:
    """A GitHub blip can answer 200 with an HTML error page: `gh` exits zero and
    the body isn't JSON. Without the parse guard the run would carry on against
    a non-JSON snapshot and fail the step.
    """
    body = tmp_path / "body.html"
    body.write_text("<html>unicorn</html>")
    notifications_env["RAW_BODY"] = str(body)

    result = _run_check(notifications_env)

    assert result.returncode == 0, result.stderr
    assert _output(notifications_env, "count") == "0"
