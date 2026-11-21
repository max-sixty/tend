"""Refuse issue/PR creation far above the repository's recent volume.

A guard against a runaway process creating issues and PRs by accident, such as
a loop filing one on every run. It is not a budget, and not a defence against an
agent working to get past it, which would have worse ways to do harm. Creations
in a rolling 24 hours may reach four times the daily average of the week before,
and at least 30. Busy days stay inside that; a loop filing an issue every few
minutes does not. Accepted volume feeds the next week's average, so steady
growth raises the limit by itself.

Each spell of refusals has its own tracker issue, which doesn't count toward
volume: a refusal comments on the open tracker, or files one when none is open,
and once it closes the next refusal files a new one. A maintainer approves more
by closing a tracker: each close in the past 24 hours doubles the limit, so a
surge they keep approving needs a few closes rather than one per allowance. Bot
and GitHub App closes approve nothing, so an agent that closes the tracker after
a refusal doesn't raise its own limit; a bot close records that creation has
recovered.

Each proxy forwards one creation at a time, so the history lists everything
it created before. Independent proxies read the same history without
reserving, so creations admitted at the same moment on two runners can
overshoot by a few. Trusted runner reports count toward volume. Other agent
operations keep running.
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
PAUSE_TITLE = "Creation allowance exhausted"
PAUSE_DESCRIPTION = "Creation allowance exhausted; close to approve more"
PAUSE_COLOR = "fbca04"
FLOOR = 30
MULTIPLE = 4
BASELINE_DAYS = 7
DAY = timedelta(days=1)
JITTER = 30
_STAMP = "%Y-%m-%dT%H:%M:%SZ"

PAUSE_BODY = f"""\
The bot has created more issues and pull requests in the past 24 hours than \
its allowance: {MULTIPLE} times its daily average over the week before, and at \
least {FLOOR}. New creations are refused; reviews, comments, existing-branch \
repairs and issue closures continue. Usage falls as creations become 24 hours \
old.

**Closing this issue approves more:** each close doubles the allowance for 24 \
hours. Refusals comment here while it is open; one after it closes files a new \
issue. This bot and GitHub Apps cannot approve themselves.

{{row}}

Refused requests were not sent to GitHub. Retry when usage falls or after an \
approval; unfinished issue and PR work remains for Tend's normal recovery sweep.
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


def _is_tracker(row: dict[str, Any]) -> bool:
    return (
        row["title"] == PAUSE_TITLE
        and "pull_request" not in row
        and any(label["name"] == PAUSE_LABEL for label in row["labels"])
    )


def _history(
    repo: str, bot: str, since: datetime, now: datetime
) -> tuple[list[datetime], list[int]]:
    """The bot's creations since *since*, trackers aside, and the trackers
    updated in the past 24 hours, which hold every close that can approve."""
    # GitHub's `since` filters updates, so discard old creations returned after
    # recent edits. Node IDs deduplicate an item a new creation pushes onto the
    # next page mid-read.
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
    created, trackers = {}, set()
    for row in _common.gh_paginated(f"repos/{repo}/issues?{query}"):
        node, at = row["node_id"], _timestamp(row["created_at"])
        if not isinstance(node, str) or not node:
            raise _common.UnexpectedShape("creation is missing its node ID")
        if _is_tracker(row):
            if _timestamp(row["updated_at"]) > now - DAY:
                trackers.add(row["number"])
        elif at >= since:
            created[node] = at
    return list(created.values()), sorted(trackers)


def limit(created: list[datetime], now: datetime) -> int:
    """Four times the daily average of the week before the last 24 hours."""
    end = now - DAY
    week = sum(end - timedelta(days=BASELINE_DAYS) < at <= end for at in created)
    return max(FLOOR, math.ceil(MULTIPLE * week / BASELINE_DAYS))


def admit(request: dict[str, Any]) -> dict[str, Any]:
    repo = _common.require_env("GITHUB_REPOSITORY")["GITHUB_REPOSITORY"]
    if "repository_id" in request:
        current_id = _common.gh_json("api", f"repos/{repo}")["node_id"]
        in_scope = request["repository_id"] == current_id
    else:
        in_scope = request["repository"].casefold() == repo.casefold()
    if not in_scope:
        return {"allowed": True, "message": ""}

    bot, bot_id = bot_identity.read()
    now = _common.utcnow()
    created, trackers = _history(
        repo, bot, now - DAY - timedelta(days=BASELINE_DAYS), now
    )
    allowance = limit(created, now)
    recent = [at for at in created if at > now - DAY]
    if len(recent) < allowance:
        return {"allowed": True, "message": ""}

    approved = sum(
        len(approvals(_approval_events(repo, number), bot_id=bot_id, now=now))
        for number in trackers
    )
    allowance *= 2**approved
    if len(recent) < allowance:
        return {"allowed": True, "message": ""}

    approval = f" after {approved} approval{'s' * (approved > 1)}" if approved else ""
    status = (
        f"{len(recent)} creations in the past 24 hours; limit {allowance}{approval}. "
        f"Next usage expiry: {(min(recent) + DAY).strftime(_STAMP)}; recheck then."
    )
    recovery = _record_refusal(f"{_issue.row()}\n\n{status}")
    return {
        "allowed": False,
        "message": (
            f"Tend creation allowance exhausted: {status} {recovery} "
            "Reviews, existing-branch repairs and issue closures can continue."
        ),
    }


def approvals(
    events: list[dict[str, Any]], *, bot_id: int, now: datetime
) -> list[datetime]:
    """Human closes in the past 24 hours."""
    result = []
    for event in events:
        if event["event"] != "closed":
            continue
        at = _timestamp(event["created_at"])
        if at <= now - DAY:
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


def _record_refusal(row: str) -> str:
    """Comment *row* on the open tracker, or file one when none is open."""
    # The refusal stands whatever happens here. A failed read cannot show that
    # no tracker is open, so it records nothing rather than file a second one.
    try:
        pause = _issue.canonical(PAUSE_LABEL, PAUSE_TITLE)
        if pause is None:
            time.sleep(random.randrange(JITTER))
            pause = _issue.canonical(PAUSE_LABEL, PAUSE_TITLE)
    except _common.GH_READ_FAILED:
        return (
            f"The `{PAUSE_LABEL}` issues could not be read, so this refusal is not "
            "recorded on a tracker; retry when usage falls."
        )
    if pause is not None:
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
                "nothing to close; retry when usage falls."
            )
    tracker = f"#{pause}" if pause is not None else f"the open `{PAUSE_LABEL}` issue"
    return (
        f"Refused runs are listed in {tracker}; a maintainer closing it approves more."
    )


def _approval_events(repo: str, issue: int) -> list[dict[str, Any]]:
    # /events omits comments and carries the label, close time and actor.
    return _common.gh_paginated(f"repos/{repo}/issues/{issue}/events?per_page=100")
