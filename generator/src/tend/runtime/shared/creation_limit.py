"""Admit agent issue/PR creations in the job's repository through one bucket.

The credential proxy calls this installed runtime before forwarding a creation.
Reviews, comments, existing-branch pushes and issue closures never call it.
Trusted runner reports can still be filed; their creations count in the history.

GitHub's repository issue listing includes PRs and supplies canonical creation
times without search-index lag. At UTC midnight a ten-item bucket starts full,
refilling at ``10 + previous_six_days_count // 3`` items per day. Replay today's
creations to read its balance. Each maintainer close of the pause tracker today
doubles both capacity and refill rate. The
bucket and approvals reset together at UTC midnight. Historical overdraw
empties the bucket without introducing a debt ledger.

Approval multipliers expire, but actual creations still inform the following
six days' refill rates. This adapts to volume and guards sudden spikes; it is
not a fixed ceiling on sustained output.

The proxy includes successful and in-flight creations not yet visible in the
listing. Node IDs deduplicate receipts; a success without an ID is counted
conservatively until it ages out. Independent runner proxies cannot reserve
atomically against GitHub, so this remains a best-effort spike guard.
The listing and the existing pause issue are the only shared state.
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
PAUSE_TITLE = "Bot rate limit reached"
PAUSE_DESCRIPTION = "Creation allowance exhausted; close to approve more"
PAUSE_COLOR = "fbca04"
BURST_ALLOWANCE = 10
BASELINE_DAYS = 6
JITTER = 30
_DAY = "%Y-%m-%d"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"

PAUSE_BODY = """\
The bot's creation allowance is exhausted. New issues and pull requests wait \
for it to refill; reviews, repairs to existing branches and issue closures can \
continue.

**Closing this issue approves more creation: it doubles both the burst \
allowance and refill rate for the rest of the UTC day.** Further closes double \
them again. Only closes by a maintainer count; this bot and GitHub Apps cannot \
approve themselves.

{row}

Refused requests were not sent to GitHub. The agent can retry after refill or \
approval; unfinished issue and PR work remains for Tend's normal recovery sweep.
"""


def main() -> int:
    request = json.load(sys.stdin)
    # Tracker helpers print workflow diagnostics; stdout is exclusively the
    # proxy protocol, including when filing a pause issue fails.
    with contextlib.redirect_stdout(sys.stderr):
        decision = admit(request)
    print(json.dumps(decision))
    return 0


def _timestamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("creation timestamp must have a timezone")
    return stamp


def _activity(repo: str, bot: str, since: datetime) -> list[dict[str, Any]]:
    # `since` filters updates, not creation. It includes every relevant
    # creation and may include old issues updated recently; filter those below.
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
    result = []
    for row in rows:
        node, created = row["node_id"], _timestamp(row["created_at"])
        if not isinstance(node, str) or not node:
            raise _common.UnexpectedShape("creation is missing its node ID")
        if created >= since:
            result.append({"id": node, "created_at": created})
    return result


def balance(
    created: list[datetime],
    now: datetime,
    capacity: int,
    per_day: int,
) -> float:
    """Replay creations with continuous refill, bounded by bucket capacity."""
    previous = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tokens = float(capacity)
    rate = per_day / 86400
    for stamp in sorted(created):
        if stamp < previous or stamp > now:
            continue
        tokens = max(
            0.0, min(capacity, tokens + (stamp - previous).total_seconds() * rate) - 1
        )
        previous = stamp
    return min(capacity, tokens + (now - previous).total_seconds() * rate)


def _allowance(
    activity: list[dict[str, Any]],
    times: list[datetime],
    now: datetime,
    approvals: int = 0,
) -> tuple[int, int, float]:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    floor = midnight - timedelta(days=BASELINE_DAYS)
    past = sum(floor <= row["created_at"] < midnight for row in activity)
    capacity = BURST_ALLOWANCE << approvals
    per_day = (10 + past // 3) << approvals
    return capacity, per_day, balance(times, now, capacity, per_day)


def admit(request: dict[str, Any]) -> dict[str, Any]:
    env = _common.require_env("GITHUB_REPOSITORY")
    repo = env["GITHUB_REPOSITORY"]
    if "repository_id" in request:
        current_id = _common.gh_json("api", f"repos/{repo}")["node_id"]
        in_scope = request["repository_id"] == current_id
    else:
        in_scope = request["repository"].casefold() == repo.casefold()
    if not in_scope:
        return {"allowed": True, "in_scope": False, "message": ""}

    bot, bot_id = bot_identity.read()
    now = _common.utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    activity = _activity(repo, bot, midnight - timedelta(days=BASELINE_DAYS))
    created = {row["id"]: row["created_at"] for row in activity}
    pending = []
    for receipt in request["pending"]:
        stamp = _timestamp(receipt["created_at"])
        if receipt["id"] is None:
            pending.append(stamp)
        else:
            created.setdefault(receipt["id"], stamp)
    times = [*created.values(), *pending]
    # Read the clock after the evidence: a slow GitHub response can cross the
    # UTC reset. The history query may then contain an extra day, which the
    # allowance's baseline window excludes.
    now = _common.utcnow()
    _, _, tokens = _allowance(activity, times, now)
    if tokens >= 1:
        return {"allowed": True, "in_scope": True, "message": ""}

    # Read approvals only when needed. A racing maintainer close is included
    # in the post-jitter read before we open or reopen a pause issue.
    pauses = _issue.matching(PAUSE_LABEL, "all", PAUSE_TITLE)
    if not pauses:
        time.sleep(random.randrange(JITTER))
        pauses = _issue.matching(PAUSE_LABEL, "all", PAUSE_TITLE)
    histories = [_approval_events(repo, number) for number in pauses]
    now = _common.utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    approvals = sum(
        count_approvals(
            events, label=PAUSE_LABEL, bot_id=bot_id, today=now.strftime(_DAY)
        )
        for events in histories
    )
    capacity, per_day, tokens = _allowance(activity, times, now, approvals)
    if tokens >= 1:
        return {"allowed": True, "in_scope": True, "message": ""}

    seconds = math.ceil((1 - tokens) * 86400 / per_day)
    retry_at = min(
        now + timedelta(seconds=seconds), midnight + timedelta(days=1)
    ).strftime(_STAMP)
    row = (
        f"{_issue.row()}\n\nCreation allowance exhausted: {capacity} burst, "
        f"{per_day} items/day refill ({approvals} approval(s)). "
        f"Next creation may be retried at {retry_at} if no other work consumes the refill."
    )
    recovery = _file_pause_issue(row, pauses[0] if pauses else None)
    return {
        "allowed": False,
        "in_scope": True,
        "message": (
            f"Tend creation allowance exhausted ({capacity} burst, {per_day}/day refill). "
            f"Retry at {retry_at} or after maintainer approval. {recovery} "
            "Reviews, existing-branch repairs and issue closures can continue."
        ),
    }


def count_approvals(
    events: list[dict[str, Any]], *, label: str, bot_id: int, today: str
) -> int:
    """Closes by a person, after the label went on, and today.

    The bucket and approvals reset at the UTC rollover. Count closes after
    the label, because otherwise moving the label onto
    an issue closed at any earlier point would import that close as an
    approval — and the bot can move labels. On the issue the limiter files
    the label goes on at creation, so this excludes nothing real.

    The two actor exclusions cover different things: the id rules out the bot,
    and ``type != "Bot"`` rules out GitHub Apps — ``github-actions[bot]``
    above all, which a workflow's own ``GITHUB_TOKEN`` acts as, and which is a
    different account that would otherwise read as a person. Excluding the
    class rather than naming apps needs no allowlist.
    """
    after_label = ""
    for event in events:
        # These fields decide whether a person approved more work. Missing
        # evidence must stop the check, not silently manufacture a verdict.
        datetime.strptime(event["created_at"], _STAMP)
        if event["event"] == "labeled" and not isinstance(event["label"]["name"], str):
            raise _common.UnexpectedShape("invalid event label")
        labelled = event["event"] == "labeled"
        if labelled and event["label"]["name"] == label:
            at = event["created_at"]
            after_label = max(after_label, at)
    since = f"{today}T00:00:00Z"
    until = (datetime.strptime(today, _DAY) + timedelta(days=1)).strftime(_STAMP)
    return sum(
        1
        for event in events
        if since <= event["created_at"] < until
        and _is_approval(event, bot_id=bot_id, since=after_label)
    )


def _is_approval(event: dict[str, Any], *, bot_id: int, since: str) -> bool:
    if event["event"] != "closed" or event["created_at"] <= since:
        return False
    actor = event["actor"]
    if (
        type(actor["id"]) is not int
        or not isinstance(actor["type"], str)
        or not actor["type"]
    ):
        raise _common.UnexpectedShape("invalid close actor")
    return not (actor["id"] == bot_id or actor["type"] == "Bot")


def _file_pause_issue(row: str, pause: int | None) -> str:
    """Reopen and append to the pause issue, or file one; say what to close.

    Returns the recovery sentence the refusal annotation ends with, because
    how far this got and what a maintainer can do about it are the same fact.
    Splitting them apart invites the one wrong answer that matters: saying
    "could not be filed" about an issue that *was* filed sends a maintainer
    away from the one thing that would restart the bot.
    """
    if pause is not None:
        # A no-op when it is already open, which is the usual case for the
        # second and later runs refused in one incident.
        try:
            _common.gh("issue", "reopen", str(pause))
        except subprocess.CalledProcessError:
            pass
        # The row is best-effort, and deliberately so: this is the common path
        # — every refusal after the first in one incident takes it — and it
        # fails under the same secondary rate limit that can refuse a create.
        # The annotation is worth more than the row: the issue already exists,
        # so it can still name what to close.
        try:
            _issue.comment(pause, row)
        except subprocess.CalledProcessError:
            _common.annotate("warning", f"Could not append this run's row to #{pause}.")
        return f"Refused runs are listed in #{pause}; closing it doubles the burst allowance and refill rate."

    _issue.ensure_label(PAUSE_LABEL, PAUSE_DESCRIPTION, PAUSE_COLOR)
    # The row twice over: it seeds the body, and it goes to the reconcile as
    # the carry-over argument, so that a leg standing down to a racing sibling
    # moves the row onto the keeper before closing. Skipping it there would
    # strand the refused run's only record in a closed duplicate while the
    # `::error::` names the survivor — and the survivor is the issue whose
    # close lifts the burst allowance and refill rate, so the one artifact a maintainer is asked to
    # act on would be the one missing the evidence.
    try:
        number = _issue.create_and_reconcile(
            PAUSE_LABEL, PAUSE_TITLE, row, PAUSE_BODY.format(row=row)
        )
    except subprocess.CalledProcessError:
        # A failed create is not far-fetched here: this runs when the bot is
        # already at abnormal volume, which is when GitHub is likeliest to
        # answer `issue create` with a secondary rate limit. It must not cost
        # the run its annotation, this run's only trace.
        return (
            "The pause issue could not be filed (see the error above), so there is "
            "nothing to close; retry when the allowance refills."
        )
    if number is None:
        # Filed, but no number came back: `gh issue create` printed something
        # other than an issue URL. The label points at the same issue just as
        # uniquely, where naming `#` would point at nothing.
        return (
            f"Refused runs are listed in the open `{PAUSE_LABEL}` issue; closing it "
            "doubles the burst allowance and refill rate."
        )
    return f"Refused runs are listed in #{number}; closing it doubles the burst allowance and refill rate."


def _approval_events(repo: str, issue: int) -> list[dict[str, Any]]:
    """Read all approval events; an unavailable history is not zero approvals.

    /events omits comments, which accumulate on every refused run, and carries
    all facts needed to count the maintainer's closes.
    """
    return _common.gh_paginated(f"repos/{repo}/issues/{issue}/events?per_page=100")
