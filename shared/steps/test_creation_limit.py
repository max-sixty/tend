from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import pytest
from tend.runtime.shared import _common, bot_identity
from tend.runtime.shared import creation_limit as limiter

from _fakes import FakeGh, GithubFiles

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
BOT_ID = 4242
REQUEST = {"repository": "owner/repo"}
TRACKERS = (
    "issue",
    "list",
    "--repo",
    "owner/repo",
    "--author",
    "@me",
    "--label",
    "tend-rate-limit",
    "--state",
    "open",
    "--limit",
    "100",
    "--json",
    "number,title",
)
EVENTS = (
    "api",
    "--paginate",
    "--slurp",
    "repos/owner/repo/issues/42/events?per_page=100",
)


def _activity(now: datetime = NOW) -> tuple[str, ...]:
    since = (now - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urlencode(
        {
            "state": "all",
            "creator": "tend-agent",
            "sort": "created",
            "direction": "desc",
            "since": since,
            "per_page": 100,
        }
    )
    return ("api", "--paginate", "--slurp", f"repos/owner/repo/issues?{query}")


def _row(number: int, at: datetime = NOW) -> dict[str, str]:
    return {"node_id": f"I_{number}", "created_at": at.isoformat(), "title": "x"}


def _tracker(number: int = 42, updated: datetime = NOW) -> dict[str, Any]:
    """A tracker the history read returns; it counts toward nothing."""
    return {
        "node_id": f"T_{number}",
        "number": number,
        "title": limiter.PAUSE_TITLE,
        "labels": [{"name": limiter.PAUSE_LABEL}],
        "created_at": (updated - timedelta(hours=1)).isoformat(),
        "updated_at": updated.isoformat(),
    }


def _rows(count: int, at: datetime = NOW, start: int = 0) -> list[dict[str, str]]:
    return [_row(start + n, at) for n in range(count)]


@pytest.fixture
def gh(
    fake_gh: FakeGh,
    github_files: GithubFiles,
    actions_env: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> FakeGh:
    monkeypatch.setattr(_common, "utcnow", lambda: NOW)
    monkeypatch.setattr(limiter.time, "sleep", lambda _: None)
    fake_gh.respond("api", "user", with_={"login": "tend-agent", "id": BOT_ID})
    fake_gh.respond("api", "repos/owner/repo", with_={"node_id": "R_current"})
    fake_gh.respond(*_activity(), with_=[[]])
    fake_gh.respond(*TRACKERS, with_=[{"number": 42, "title": limiter.PAUSE_TITLE}])
    fake_gh.respond(*EVENTS, with_=[[]])
    fake_gh.respond("issue", "comment", with_="")
    return fake_gh


def _history(gh: FakeGh, rows: list[dict[str, Any]], now: datetime = NOW) -> None:
    # Two pages ensure the operation doesn't silently inspect only page one.
    gh.respond(*_activity(now), with_=[rows[:5], rows[5:]])


def _closed(
    at: datetime = NOW - timedelta(hours=1),
    actor_id: int = 99,
    actor_type: str = "User",
) -> dict[str, Any]:
    return {
        "event": "closed",
        "created_at": at.isoformat(),
        "actor": {"login": "maintainer", "id": actor_id, "type": actor_type},
    }


def test_identity_resolution_does_not_read_creation_history(
    gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    assert bot_identity.main() == 0
    assert github_files.outputs() == {"login": "tend-agent", "id": str(BOT_ID)}
    assert gh.calls == [("api", "user")]


def test_quiet_repository_gets_the_floor(gh: FakeGh) -> None:
    _history(gh, _rows(29, NOW - timedelta(hours=4)))
    assert limiter.admit(REQUEST)["allowed"]
    assert not gh.called("issue")
    _history(gh, _rows(30, NOW - timedelta(hours=4)))
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert "30 creations in the past 24 hours; limit 30." in decision["message"]
    assert "#42" in decision["message"]
    assert gh.called("issue", "comment", "42")


def test_limit_is_four_times_the_previous_weeks_daily_average(gh: FakeGh) -> None:
    week = _rows(140, NOW - timedelta(days=3))  # ceil(4 * 140 / 7) = 80
    older = _rows(500, NOW - timedelta(days=8, seconds=1), start=1000)
    _history(gh, [*week, *older, *_rows(79, start=2000)])
    assert limiter.admit(REQUEST)["allowed"]
    _history(gh, [*week, *older, *_rows(80, start=2000)])
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert "limit 80." in decision["message"]


def test_rolling_usage_survives_midnight_and_expires_after_24_hours(
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = NOW.replace(hour=23, minute=59)
    for now in (at, at + timedelta(minutes=2), at + timedelta(days=1)):
        _history(gh, _rows(30, at), now)
    monkeypatch.setattr(_common, "utcnow", lambda: at)
    assert not limiter.admit(REQUEST)["allowed"]
    monkeypatch.setattr(_common, "utcnow", lambda: at + timedelta(minutes=2))
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert "Next usage expiry: 2026-01-03T23:59:00Z" in decision["message"]
    monkeypatch.setattr(_common, "utcnow", lambda: at + timedelta(days=1))
    assert limiter.admit(REQUEST)["allowed"]


def test_each_human_close_doubles_the_limit(gh: FakeGh) -> None:
    closes = [_closed(NOW - timedelta(hours=h)) for h in (1, 2, 3)]
    gh.respond(*EVENTS, with_=[closes])
    _history(gh, [_tracker(), *_rows(239, NOW - timedelta(hours=4))])
    assert limiter.admit(REQUEST)["allowed"]
    _history(gh, [_tracker(), *_rows(240, NOW - timedelta(hours=4))])
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert (
        "240 creations in the past 24 hours; limit 240 after 3 approvals."
        in decision["message"]
    )


@pytest.mark.parametrize(
    "approval",
    [
        _closed(actor_id=BOT_ID),
        _closed(actor_type="Bot"),
        _closed(NOW - timedelta(days=1)) | {"actor": None},
    ],
    ids=["bot", "app", "older-than-a-day"],
)
def test_only_a_recent_human_close_approves(gh: FakeGh, approval: dict) -> None:
    _history(gh, [_tracker(), *_rows(30)])
    gh.respond(*EVENTS, with_=[[approval]])
    assert not limiter.admit(REQUEST)["allowed"]
    assert gh.called(*EVENTS)


def test_trackers_are_left_out_of_the_count(gh: FakeGh) -> None:
    """The limiter files a tracker per spell of refusals, and counting it would
    make the spell pay for its own notice."""
    _history(gh, [_tracker(41), _tracker(42), *_rows(29)])
    assert limiter.admit(REQUEST)["allowed"]
    _history(gh, [_tracker(41), _tracker(42), *_rows(30)])
    gh.respond(*EVENTS[:3], EVENTS[3].replace("/42/", "/41/"), with_=[[]])
    assert not limiter.admit(REQUEST)["allowed"]


def test_a_failed_tracker_read_still_refuses_and_files_nothing(gh: FakeGh) -> None:
    _history(gh, _rows(30))
    gh.respond(*TRACKERS, with_=1)
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert "30 creations in the past 24 hours; limit 30." in decision["message"]
    assert "could not be read" in decision["message"]
    assert not gh.called("issue", "create")
    assert not gh.called("issue", "comment")


def test_a_refusal_after_the_tracker_closed_files_a_new_one(gh: FakeGh) -> None:
    """A tracker filed over a day ago and closed has ended its spell: the next
    refusal files a new tracker rather than reopening it, and a tracker
    untouched for a day holds no close that can still approve, so its events
    are not read."""
    old = _tracker(42, updated=NOW - timedelta(days=2)) | {"state": "closed"}
    _history(gh, [old, *_rows(30)])
    gh.respond(*TRACKERS, with_=[])
    gh.respond("label", "create", with_="")
    gh.respond("issue", "create", with_="https://github.com/owner/repo/issues/43\n")
    for number in range(33, 42):
        gh.respond("api", f"repos/owner/repo/issues/{number}", with_=1)
    gh.respond("api", "repos/owner/repo/issues/42", with_=old)

    decision = limiter.admit(REQUEST)

    assert not decision["allowed"]
    assert "Refused runs are listed in #43" in decision["message"]
    assert not gh.called(*EVENTS)
    assert not gh.called("issue", "comment")
    assert not gh.called("issue", "reopen")


@pytest.mark.parametrize(
    "creation,read",
    [
        ({"repository": "OWNER/REPO"}, True),
        ({"repository": "other/repo"}, False),
        ({"repository_id": "R_current"}, True),
        ({"repository_id": "R_other"}, False),
    ],
)
def test_rest_and_graphql_share_the_job_repository_scope(
    gh: FakeGh, creation: dict, read: bool
) -> None:
    _history(gh, _rows(30))
    assert limiter.admit(creation)["allowed"] is not read
    assert bool(gh.called(*_activity())) is read


@pytest.mark.parametrize(
    "response", [1, {}, [[{"node_id": None, "created_at": NOW.isoformat()}]]]
)
def test_unavailable_or_invalid_history_cannot_admit_or_file(
    gh: FakeGh, response: Any
) -> None:
    gh.respond(*_activity(), with_=response)
    with pytest.raises((*_common.GH_READ_FAILED, KeyError, TypeError, ValueError)):
        limiter.admit(REQUEST)
    assert not gh.called("issue")


def test_cli_stdout_is_only_the_proxy_decision(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _history(gh, _rows(30))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(REQUEST)))
    assert limiter.main() == 0
    assert not json.loads(capsys.readouterr().out)["allowed"]


def test_missing_notice_does_not_claim_a_tracker_exists(gh: FakeGh) -> None:
    _history(gh, _rows(30))
    gh.respond(*TRACKERS, with_=[])
    gh.respond("label", "create", with_="")
    gh.respond("issue", "create", with_=1)
    result = limiter.admit(REQUEST)
    assert not result["allowed"]
    assert "could not be filed" in result["message"]
    assert "#?" not in result["message"]


@pytest.mark.parametrize(
    "age,accepted",
    [
        (timedelta(days=1), False),
        (timedelta(days=1) - timedelta(seconds=1), True),
        (timedelta(0), True),
    ],
)
def test_approval_window_boundaries(age: timedelta, accepted: bool) -> None:
    at = NOW - age
    assert limiter.approvals([_closed(at)], bot_id=BOT_ID, now=NOW) == (
        [at] if accepted else []
    )
