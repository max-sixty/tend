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
        "since": "2025-12-27T00:00:00Z",
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
    _history(gh, [_row(n) for n in range(14)])
    decision = limiter.admit(REQUEST)
    assert not decision["allowed"]
    assert "10 burst, 10/day refill" in decision["message"]
    assert "#42" in decision["message"]
    assert gh.called("issue", "reopen", "42")
    gh.respond(
        "api",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/42/events?per_page=100",
        with_=[[_closed()]],
    )
    assert limiter.admit(REQUEST)["allowed"]


@pytest.mark.parametrize(
    "approval",
    [
        _closed("renamed-bot", actor_id=BOT_ID),
        _closed("github-actions[bot]", actor_type="Bot"),
        _closed(day="2026-01-01"),
    ],
)
def test_creation_cannot_use_bot_or_expired_approval(
    gh: FakeGh, approval: dict
) -> None:
    _history(gh, [_row(n) for n in range(14)])
    gh.respond(
        "api",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/42/events?per_page=100",
        with_=[[approval]],
    )
    assert not limiter.admit(REQUEST)["allowed"]


def test_refill_recovers_without_a_new_approval(gh: FakeGh) -> None:
    _history(gh, [_row(n, NOW - timedelta(hours=3)) for n in range(10)])
    assert limiter.admit(REQUEST)["allowed"]
    assert not gh.called("issue")


def test_midnight_resets_the_bucket_and_bounds_the_retry_time(
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late = NOW.replace(hour=23, minute=59)
    _history(gh, [_row(n, late) for n in range(10)])
    monkeypatch.setattr(_common, "utcnow", lambda: late)
    result = limiter.admit(REQUEST)
    assert not result["allowed"]
    assert "Retry at 2026-01-03T00:00:00Z" in result["message"]
    assert limiter.balance([late] * 10, late + timedelta(minutes=1), 10, 10) == 10


def test_sustained_work_consumes_refill_without_a_large_single_burst() -> None:
    # A creation every half hour exceeds the refill rate without a single burst.
    events = [NOW - timedelta(hours=n / 2) for n in range(24)]
    assert limiter.balance(events, NOW, 10, 10) < 1
    # The same work fits the approved rate, and the clock refills continuously.
    assert limiter.balance(events, NOW, 20, 20) >= 1


def test_baseline_controls_refill_and_old_updated_issues_do_not_count(
    gh: FakeGh,
) -> None:
    _history(
        gh,
        [
            *[_row(n, NOW - timedelta(days=2)) for n in range(90)],
            _row(1000, NOW - timedelta(days=100)),
            *[_row(n + 100, NOW - timedelta(hours=1)) for n in range(10)],
        ],
    )
    assert limiter.admit(REQUEST)["allowed"]  # refill is 40/day, not 10/day


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


@pytest.mark.parametrize(
    ("events", "expected", "why"),
    [
        ([_closed("maintainer")], 1, "a person's close today is an approval"),
        ([_closed("maintainer"), _closed("other")], 2, "each close doubles again"),
        # A renamed account is still the bot: the account is an ordinary user
        # account, so the type check does nothing for it and the id is the
        # whole control. Matching on a name would fail open the moment the
        # account were renamed — an actor matching nothing reads as a person.
        ([_closed("tend-agent-renamed", actor_id=BOT_ID)], 0, "the bot, renamed"),
        # `github-actions[bot]`, which a workflow's own GITHUB_TOKEN acts as,
        # is a different account that the id check alone would let through.
        ([_closed("github-actions[bot]", actor_type="Bot")], 0, "a GitHub App"),
        # Approvals are scoped to today, since the count they lift resets at
        # the UTC rollover.
        ([_closed("maintainer", day="2026-01-01")], 0, "yesterday's close"),
        (
            [
                _closed("deleted", day="2026-01-01") | {"actor": None},
                _closed("maintainer"),
            ],
            1,
            "an old deleted actor cannot invalidate today's approval",
        ),
    ],
)
def test_count_approvals_takes_only_a_persons_close_today(
    events: list[dict[str, Any]], expected: int, why: str
) -> None:
    labelled = {
        "event": "labeled",
        "label": {"name": limiter.PAUSE_LABEL},
        "created_at": "2026-01-01T08:00:00Z",
    }
    counted = limiter.count_approvals(
        [labelled, *events], label=limiter.PAUSE_LABEL, bot_id=BOT_ID, today=TODAY
    )
    assert counted == expected, why


def test_count_approvals_ignores_a_foreign_labels_event() -> None:
    """Only this record's label moves the floor.

    Any label can go on this issue, so a `labeled` event for an unrelated one
    later today would otherwise raise the floor past a genuine approval and
    silently drop it.
    """
    foreign = {
        "event": "labeled",
        "label": {"name": "needs-triage"},
        "created_at": f"{TODAY}T23:00:00Z",
    }
    events = [foreign, _closed("maintainer")]
    assert (
        limiter.count_approvals(
            events, label=limiter.PAUSE_LABEL, bot_id=BOT_ID, today=TODAY
        )
        == 1
    )


def test_count_approvals_ignores_closes_that_predate_the_label() -> None:
    """Moving the label onto an already-closed issue grants nothing.

    The bot holds `issues: write`, so it can label any issue. Were approvals
    counted from the whole history, labelling one a maintainer had closed
    earlier today would import that close as an approval nobody gave. On a
    real pause issue the label goes on at creation, so nothing genuine is
    excluded.
    """
    labelled = {
        "event": "labeled",
        "label": {"name": limiter.PAUSE_LABEL},
        "created_at": f"{TODAY}T10:00:00Z",
    }
    events = [labelled, _closed("maintainer")]  # closed at 09:00, an hour earlier
    assert (
        limiter.count_approvals(
            events, label=limiter.PAUSE_LABEL, bot_id=BOT_ID, today=TODAY
        )
        == 0
    )


@pytest.mark.parametrize("slow_read", ["activity", "approvals"])
def test_midnight_during_evidence_reads_expires_approval(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch, slow_read: str
) -> None:
    late = NOW.replace(hour=23, minute=59, second=59)
    next_day = late + timedelta(seconds=1)
    monkeypatch.setattr(_common, "utcnow", lambda: late)
    _history(
        gh,
        [
            # Included when the query starts, outside the baseline after midnight.
            *[
                _row(n + 1000, NOW.replace(hour=0) - timedelta(days=6))
                for n in range(30)
            ],
            *[_row(n, late) for n in range(14)],
            *[_row(n + 100, next_day) for n in range(10)],
        ],
    )
    gh.respond(
        "api",
        "--paginate",
        "--slurp",
        "repos/owner/repo/issues/42/events?per_page=100",
        with_=[[_closed()]],
    )
    # GitHub is the mocked boundary: its response arrives on the next UTC day.
    original = _common.gh_paginated

    def read(endpoint: str):
        rows = original(endpoint)
        if ("/events?" in endpoint) == (slow_read == "approvals"):
            monkeypatch.setattr(_common, "utcnow", lambda: next_day)
        return rows

    monkeypatch.setattr(_common, "gh_paginated", read)
    result = limiter.admit(REQUEST)
    assert not result["allowed"]
    assert "10 burst, 14/day refill" in result["message"]
    assert "2026-01-03T01:42:52Z" in result["message"]


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [("2026-01-02T00:00:00Z", 1), ("2026-01-03T00:00:00Z", 0)],
)
def test_approval_day_includes_its_start_and_excludes_the_next(
    stamp: str, expected: int
) -> None:
    assert (
        limiter.count_approvals(
            [_closed() | {"created_at": stamp}],
            label=limiter.PAUSE_LABEL,
            bot_id=BOT_ID,
            today=TODAY,
        )
        == expected
    )
