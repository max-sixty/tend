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
TODAY = "2026-01-02"
BOT_ID = 4242
REQUEST = {"repository": "owner/repo", "pending": []}
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
    "all",
    "--limit",
    "100",
    "--json",
    "number,title",
)
ACTIVITY = "repos/owner/repo/issues?" + urlencode(
    {
        "state": "all",
        "creator": "tend-agent",
        "sort": "created",
        "direction": "desc",
        "since": "2025-12-19T00:00:00Z",
        "per_page": 100,
    }
)


def _row(number: int, at: datetime = NOW) -> dict[str, str]:
    return {"node_id": f"I_{number}", "created_at": at.isoformat()}


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
    fake_gh.respond("api", "--paginate", "--slurp", ACTIVITY, with_=[[]])
    fake_gh.respond(*TRACKERS, with_=[{"number": 42, "title": limiter.PAUSE_TITLE}])
    fake_gh.respond(
        "api",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/42/events?per_page=100",
        with_=[[]],
    )
    fake_gh.respond("issue", "reopen", with_="")
    fake_gh.respond("issue", "comment", with_="")
    return fake_gh


def _history(gh: FakeGh, rows: list[dict[str, Any]]) -> None:
    # Two pages ensure the operation doesn't silently inspect only page one.
    gh.respond("api", "--paginate", "--slurp", ACTIVITY, with_=[rows[:5], rows[5:]])


def _closed(
    login: str = "maintainer",
    actor_type: str = "User",
    day: str = TODAY,
    actor_id: int = 99,
) -> dict[str, Any]:
    return {
        "event": "closed",
        "created_at": f"{day}T09:00:00Z",
        "actor": {"login": login, "id": actor_id, "type": actor_type},
    }


def test_identity_resolution_does_not_read_creation_history(
    gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    assert bot_identity.main() == 0
    assert github_files.outputs() == {"login": "tend-agent", "id": str(BOT_ID)}
    assert gh.calls == [("api", "user")]


def test_creation_uses_paginated_history_and_one_approval_path(gh: FakeGh) -> None:
    _history(gh, [_row(n, NOW - timedelta(hours=4)) for n in range(14)])
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert "14 creations in the past 24 hours; limit 10" in decision["message"]
    assert "approval floor none" in decision["message"]
    assert "#42" in decision["message"]
    assert gh.called("issue", "reopen", "42")
    _approvals(gh, [_closed()])
    assert limiter.admit(REQUEST)["allowed"]


def _approvals(gh: FakeGh, events: list[dict]) -> None:
    gh.respond(
        "api",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/42/events?per_page=100",
        with_=[events],
    )


def test_rolling_usage_survives_midnight_and_expires_after_24_hours(
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = NOW.replace(hour=23, minute=59)
    _history(gh, [_row(n, at) for n in range(10)])
    monkeypatch.setattr(_common, "utcnow", lambda: at)
    assert not limiter.admit(REQUEST)["allowed"]
    monkeypatch.setattr(_common, "utcnow", lambda: at + timedelta(minutes=2))
    endpoint = ACTIVITY.replace("2025-12-19", "2025-12-20")
    gh.respond(
        "api",
        "--paginate",
        "--slurp",
        endpoint,
        with_=[[_row(n, at) for n in range(10)]],
    )
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert "Next usage expiry: 2026-01-03T23:59:00Z" in decision["message"]
    monkeypatch.setattr(_common, "utcnow", lambda: at + timedelta(days=1))
    assert limiter.admit(REQUEST)["allowed"]


def test_traffic_baseline_allows_a_batch_without_fixed_burst_cap(gh: FakeGh) -> None:
    _history(
        gh,
        [
            *[_row(n, NOW - timedelta(days=2)) for n in range(71)],
            _row(1000, NOW - timedelta(days=100)),
            _row(1001, NOW + timedelta(seconds=1)),
            *[_row(n + 100) for n in range(20)],
        ],
    )
    assert limiter.admit(REQUEST)["allowed"]  # ceil(142 / 7) = 21
    assert not gh.called("issue")


def test_approval_holds_its_floor_when_the_traffic_baseline_falls(gh: FakeGh) -> None:
    # The oldest active close needs all its completed baseline days, including
    # the calendar-day prefix a rolling fourteen-day fetch would omit.
    old = NOW.replace(hour=0) - timedelta(days=14)
    rows = [*[_row(n, old) for n in range(140)], *[_row(n + 200) for n in range(30)]]
    _history(gh, rows)
    _approvals(gh, [_closed() | {"created_at": "2025-12-26T12:00:01Z"}])
    assert limiter.admit(REQUEST)["allowed"]  # held floor 80, current base 10


def test_repeated_approval_uses_observed_raised_usage_and_keeps_its_own_expiry() -> (
    None
):
    first = NOW - timedelta(days=6)
    second = NOW - timedelta(days=1)
    created = [first - timedelta(minutes=1)] * 10 + [second - timedelta(minutes=1)] * 20
    assert limiter.approval_floor(created, [first], NOW) == (
        20,
        first + timedelta(days=7),
    )
    assert limiter.approval_floor(created, [first, second], NOW) == (
        40,
        second + timedelta(days=7),
    )
    assert limiter.approval_floor(
        created, [first, second], second + timedelta(days=7)
    ) == (0, None)


def test_denial_reports_persistent_floor_and_its_expiry(gh: FakeGh) -> None:
    _history(gh, [_row(n) for n in range(20)])
    _approvals(gh, [_closed()])
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert (
        "limit 20 (traffic baseline 10, approval floor 20 until 2026-01-09T09:00:00Z)"
        in decision["message"]
    )


@pytest.mark.parametrize(
    "approval",
    [
        _closed("renamed-bot", actor_id=BOT_ID),
        _closed("github-actions[bot]", actor_type="Bot"),
        _closed(day="2025-12-26")
        | {"created_at": "2025-12-26T12:00:00Z", "actor": None},
        _closed(day="2026-01-03"),
    ],
)
def test_creation_cannot_use_bot_expired_or_future_approval(
    gh: FakeGh, approval: dict
) -> None:
    _history(gh, [_row(n) for n in range(14)])
    _approvals(gh, [approval])
    assert not limiter.admit(REQUEST)["allowed"]


def test_old_policy_tracker_does_not_grant_a_new_approval(gh: FakeGh) -> None:
    _history(gh, [_row(n) for n in range(14)])
    gh.respond(*TRACKERS, with_=[{"number": 42, "title": "Bot rate limit reached"}])
    _approvals(gh, [_closed()])
    gh.respond("label", "create", with_="")
    gh.respond("issue", "create", with_=1)
    assert not limiter.admit(REQUEST)["allowed"]
    assert not gh.called(
        "api", "--paginate", "--slurp", "repos/owner/repo/issues/42/events?per_page=100"
    )


def test_receipts_deduplicate_visible_creations_and_reserve_in_flight_work(
    gh: FakeGh,
) -> None:
    rows = [_row(n) for n in range(9)]
    _history(gh, rows)
    receipts = [{"id": r["node_id"], "created_at": r["created_at"]} for r in rows]
    assert limiter.admit(REQUEST | {"pending": receipts})["allowed"]
    receipts.append({"id": None, "created_at": NOW.isoformat()})
    assert not limiter.admit(REQUEST | {"pending": receipts})["allowed"]


@pytest.mark.parametrize(
    "creation,in_scope",
    [
        ({"repository": "OWNER/REPO", "pending": []}, True),
        ({"repository": "other/repo", "pending": []}, False),
        ({"repository_id": "R_current", "pending": []}, True),
        ({"repository_id": "R_other", "pending": []}, False),
    ],
)
def test_rest_and_graphql_share_the_job_repository_scope(
    gh: FakeGh, creation: dict, in_scope: bool
) -> None:
    assert limiter.admit(creation) == {
        "allowed": True,
        "in_scope": in_scope,
        "message": "",
    }


@pytest.mark.parametrize(
    "response", [1, {}, [[{"node_id": None, "created_at": NOW.isoformat()}]]]
)
def test_unavailable_or_invalid_history_cannot_admit_or_file(
    gh: FakeGh, response: Any
) -> None:
    gh.respond("api", "--paginate", "--slurp", ACTIVITY, with_=response)
    with pytest.raises((*_common.GH_READ_FAILED, KeyError, TypeError, ValueError)):
        limiter.admit(REQUEST)
    assert not gh.called("issue")


def test_cli_stdout_is_only_the_proxy_decision(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _history(gh, [_row(n) for n in range(10)])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(REQUEST)))
    assert limiter.main() == 0
    assert not json.loads(capsys.readouterr().out)["allowed"]


def test_missing_notice_does_not_claim_a_tracker_exists(gh: FakeGh) -> None:
    _history(gh, [_row(n) for n in range(10)])
    gh.respond(*TRACKERS, with_=[])
    gh.respond("label", "create", with_="")
    gh.respond("issue", "create", with_=1)
    result = limiter.admit(REQUEST)
    assert not result["allowed"]
    assert "could not be filed" in result["message"]
    assert "#?" not in result["message"]


def test_approval_follows_its_own_label_and_excludes_prelabel_closes() -> None:
    foreign = {
        "event": "labeled",
        "label": {"name": "needs-triage"},
        "created_at": f"{TODAY}T10:00:00Z",
    }
    assert limiter.approval_times([foreign, _closed()], bot_id=BOT_ID, now=NOW) == [
        NOW.replace(hour=9)
    ]
    own = foreign | {"label": {"name": limiter.PAUSE_LABEL}}
    assert limiter.approval_times([own, _closed()], bot_id=BOT_ID, now=NOW) == []


@pytest.mark.parametrize(
    "age,accepted",
    [
        (timedelta(days=7), False),
        (timedelta(days=7) - timedelta(seconds=1), True),
        (timedelta(0), True),
        (timedelta(seconds=-1), False),
    ],
)
def test_approval_window_boundaries(age: timedelta, accepted: bool) -> None:
    at = NOW - age
    event = _closed() | {"created_at": at.isoformat()}
    assert limiter.approval_times([event], bot_id=BOT_ID, now=NOW) == (
        [at] if accepted else []
    )


def test_renamed_tracker_cannot_import_closes_from_its_previous_policy() -> None:
    events = [
        {
            "event": "labeled",
            "label": {"name": limiter.PAUSE_LABEL},
            "created_at": "2025-12-20T00:00:00Z",
        },
        _closed(day="2026-01-01"),
        {"event": "renamed", "created_at": "2026-01-02T08:00:00Z"},
    ]
    assert limiter.approval_times(events, bot_id=BOT_ID, now=NOW) == []
    events.append(_closed())
    assert limiter.approval_times(events, bot_id=BOT_ID, now=NOW) == [
        NOW.replace(hour=9)
    ]


def test_expiry_during_an_evidence_read_does_not_extend_the_floor(
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _history(gh, [_row(n) for n in range(14)])
    at = NOW - timedelta(days=7) + timedelta(seconds=1)
    _approvals(gh, [_closed() | {"created_at": at.isoformat()}])
    original = _common.gh_paginated

    def read(endpoint: str):
        rows = original(endpoint)
        if "/events?" in endpoint:
            monkeypatch.setattr(_common, "utcnow", lambda: NOW + timedelta(seconds=2))
        return rows

    monkeypatch.setattr(_common, "gh_paginated", read)
    assert not limiter.admit(REQUEST)["allowed"]
