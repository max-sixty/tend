"""Limit issue/PR creation using recent traffic and temporary approval floors.

The proxy reserves in-flight creations and reconciles successful node IDs with
GitHub's issue listing (which includes PRs). At most twice the preceding seven
completed UTC days' average, rounded up with a minimum of ten, may be created
in a rolling 24 hours. Trusted runner reports count in that history too.

A human close of the current-policy tracker grants a seven-day floor: twice
the larger of the traffic baseline and observed 24-hour usage at that close.
This approximates doubling the exhausted limit without replaying approval
chains or trusting editable tracker text. The largest unexpired floor wins.
Old-policy tracker closes retain their original meaning and grant no floor.

Independent proxies cannot reserve atomically against GitHub. The limiter is
a best-effort surge guard, not a fixed ceiling on sustained growth: accepted
volume contributes to future baselines. Other agent operations keep running.
"""

from __future__ import annotations

import contextlib
import json
import math
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from tend.runtime.shared import _common, _issue, bot_identity

PAUSE_LABEL = "tend-rate-limit"
PAUSE_TITLE = "Creation allowance reached"
PAUSE_DESCRIPTION = "Creation allowance exhausted; close to approve more"
PAUSE_COLOR = "fbca04"
BASELINE_DAYS = 7
APPROVAL_DAYS = 7
JITTER = 30
_STAMP = "%Y-%m-%dT%H:%M:%SZ"

PAUSE_BODY = """\
The bot has reached its rolling 24-hour allowance for new issues and pull \
requests. Reviews, comments, existing-branch repairs and issue closures can \
continue. Usage falls as creations become 24 hours old.

**Closing this issue sets a seven-day approval floor:** twice the \
larger of the traffic baseline and observed 24-hour creation count at closure. \
This approximates doubling the limit just reached. Each approval holds that \
floor until its own expiry, even if traffic later falls. This bot and GitHub \
Apps cannot approve themselves.

{row}

Refused requests were not sent to GitHub. Retry when usage or the allowance \
changes; unfinished issue and PR work remains for Tend's normal recovery sweep.
"""


def main() -> int:
    request = json.load(sys.stdin)
    with contextlib.redirect_stdout(sys.stderr):
        decision = admit(request)
    print(json.dumps(decision))
    return 0


def _timestamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("creation timestamp must have a timezone")
    return stamp


def _activity(repo: str, bot: str, since: datetime) -> dict[str, datetime]:
    # GitHub's `since` filters updates, so discard old creations returned after
    # recent edits. Node IDs also deduplicate pages and local proxy receipts.
    query = urlencode(
        {
            "state": "all",
            "creator": bot,
            "sort": "created",
            "direction": "desc",
            "since": since.strftime(_STAMP),
            "per_page": 100,
        }
    )
    rows = _common.gh_paginated(f"repos/{repo}/issues?{query}")
    result = {}
    for row in rows:
        node, created = row["node_id"], _timestamp(row["created_at"])
        if not isinstance(node, str) or not node:
            raise _common.UnexpectedShape("creation is missing its node ID")
        if created >= since:
            result[node] = created
    return result


def base_limit(created: list[datetime], now: datetime) -> int:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    past = sum(
        midnight - timedelta(days=BASELINE_DAYS) <= at < midnight for at in created
    )
    return max(10, math.ceil(2 * past / BASELINE_DAYS))


def recent_creations(created: list[datetime], now: datetime) -> list[datetime]:
    return [at for at in created if now - timedelta(days=1) < at <= now]


def approval_floor(
    created: list[datetime], approvals: list[datetime], now: datetime
) -> tuple[int, datetime | None]:
    floors = [
        (
            2 * max(base_limit(created, at), len(recent_creations(created, at))),
            at + timedelta(days=APPROVAL_DAYS),
        )
        for at in approvals
        if now - timedelta(days=APPROVAL_DAYS) < at <= now
    ]
    return max(floors) if floors else (0, None)


def admit(request: dict[str, Any]) -> dict[str, Any]:
    repo = _common.require_env("GITHUB_REPOSITORY")["GITHUB_REPOSITORY"]
    if "repository_id" in request:
        current_id = _common.gh_json("api", f"repos/{repo}")["node_id"]
        in_scope = request["repository_id"] == current_id
    else:
        in_scope = request["repository"].casefold() == repo.casefold()
    if not in_scope:
        return {"allowed": True, "in_scope": False, "message": ""}

    bot, bot_id = bot_identity.read()
    now = _common.utcnow()
    # Include complete baseline days for the oldest still-active approval.
    since = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=BASELINE_DAYS + APPROVAL_DAYS
    )
    activity = _activity(repo, bot, since)
    history = list(activity.values())
    created = dict(activity)
    pending = []
    for receipt in request["pending"]:
        stamp = _timestamp(receipt["created_at"])
        if receipt["id"] is None:
            pending.append(stamp)
        else:
            created.setdefault(receipt["id"], stamp)
    times = [*created.values(), *pending]
    now = _common.utcnow()
    if len(recent_creations(times, now)) < base_limit(history, now):
        return {"allowed": True, "in_scope": True, "message": ""}

    pauses = _issue.matching(PAUSE_LABEL, "all", PAUSE_TITLE)
    if not pauses:
        time.sleep(random.randrange(JITTER))
        pauses = _issue.matching(PAUSE_LABEL, "all", PAUSE_TITLE)
    histories = [_approval_events(repo, number) for number in pauses]
    now = _common.utcnow()
    approvals = [
        at
        for events in histories
        for at in approval_times(events, bot_id=bot_id, now=now)
    ]
    base = base_limit(history, now)
    approved, expires = approval_floor(history, approvals, now)
    limit = max(base, approved)
    recent = recent_creations(times, now)
    if len(recent) < limit:
        return {"allowed": True, "in_scope": True, "message": ""}

    next_expiry = (min(recent) + timedelta(days=1)).strftime(_STAMP)
    floor_note = f"{approved} until {expires.strftime(_STAMP)}" if expires else "none"
    status = (
        f"{len(recent)} creations in the past 24 hours; limit {limit} "
        f"(traffic baseline {base}, approval floor {floor_note}). "
        f"Next usage expiry: {next_expiry}; recheck then, as the allowance can also change."
    )
    recovery = _file_pause_issue(
        f"{_issue.row()}\n\n{status}", pauses[0] if pauses else None
    )
    return {
        "allowed": False,
        "in_scope": True,
        "message": (
            f"Tend creation allowance exhausted: {status} {recovery} "
            "Reviews, existing-branch repairs and issue closures can continue."
        ),
    }


def approval_times(
    events: list[dict[str, Any]], *, bot_id: int, now: datetime
) -> list[datetime]:
    """Human closes after the current tracker identity, excluding bots and apps."""
    boundary = None
    for event in events:
        at = _timestamp(event["created_at"])
        establishes_policy = event["event"] == "renamed"
        if event["event"] == "labeled":
            label = event["label"]["name"]
            if not isinstance(label, str):
                raise _common.UnexpectedShape("invalid event label")
            establishes_policy = label == PAUSE_LABEL
        # Matching already verified the current title. Its latest rename
        # prevents importing closes from a tracker with a different policy.
        if establishes_policy and at <= now:
            boundary = max(boundary, at) if boundary else at
    result = []
    for event in events:
        at = _timestamp(event["created_at"])
        if (
            event["event"] != "closed"
            or not now - timedelta(days=APPROVAL_DAYS) < at <= now
            or (boundary is not None and at <= boundary)
        ):
            continue
        actor = event["actor"]
        if (
            type(actor["id"]) is not int
            or not isinstance(actor["type"], str)
            or not actor["type"]
        ):
            raise _common.UnexpectedShape("invalid close actor")
        if actor["id"] != bot_id and actor["type"] != "Bot":
            result.append(at)
    return result


def _file_pause_issue(row: str, pause: int | None) -> str:
    if pause is not None:
        try:
            _common.gh("issue", "reopen", str(pause))
        except subprocess.CalledProcessError:
            pass
        try:
            _issue.comment(pause, row)
        except subprocess.CalledProcessError:
            _common.annotate("warning", f"Could not append this run's row to #{pause}.")
    else:
        _issue.ensure_label(PAUSE_LABEL, PAUSE_DESCRIPTION, PAUSE_COLOR)
        try:
            pause = _issue.create_and_reconcile(
                PAUSE_LABEL, PAUSE_TITLE, row, PAUSE_BODY.format(row=row)
            )
        except subprocess.CalledProcessError:
            return (
                "The pause issue could not be filed (see the error above), so there is "
                "nothing to close; retry when usage or the allowance changes."
            )
    tracker = f"#{pause}" if pause is not None else f"the open `{PAUSE_LABEL}` issue"
    return f"Refused runs are listed in {tracker}; closing it sets a seven-day approval floor."


def _approval_events(repo: str, issue: int) -> list[dict[str, Any]]:
    # /events omits comments and carries the label, close time and actor.
    return _common.gh_paginated(f"repos/{repo}/issues/{issue}/events?per_page=100")
