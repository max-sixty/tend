"""Abort the run when the bot is creating issues/PRs faster than it should.

Two limits, shared verbatim by both harness actions: a burst limit (20-minute
window) and a daily spike limit (today's volume against a 6-day baseline).

The spike limit is resumable. When it trips, the run files or reopens a
``tend-rate-limit`` issue naming the run it refused, and a maintainer closing
that issue doubles the ceiling for the rest of the UTC day; each further close
doubles it again. Two things follow from that shape:

- Opening the issue is the notice. A bare ``::error::`` annotation lands on a
  job nobody opens, so before this the repo could stop reviewing for hours
  with no signal but a red check on unrelated PRs.
- Closes by the bot itself do not count, which is what makes this a check
  rather than an instruction the bot could be talked out of. GitHub lets only
  the author or a triage/write collaborator close an issue, and the bot is the
  author — so excluding the bot leaves exactly the maintainers, with no
  allowlist to keep.

A doubling rather than a flat increment because the ceiling it lifts is itself
proportional (``10 + 2 * daily_avg``): a repo filing 15 a day would need a
close every hour to get through a legitimate spike on a flat bump. At the
formula's floor the two coincide — 10 doubles to 20 — so they only diverge
where a flat bump stops working.

Activity counts use one GraphQL query, scoped to the authenticated bot and
repository, rather than four requests against REST's smaller search budget.
Failed, partial or malformed reads stop the run before it creates or reopens
a pause issue.
Approvals on duplicate trackers count too; the oldest receives new refusals.

The burst limit is deliberately not resumable: ten PRs in twenty minutes is a
loop rather than a busy day, and there is nothing there to wave through. Its
issue count leaves out the cause issues triage files for a CI failure report
(``CI_CAUSE_LABEL``): one red nightly run can hold a dozen causes, each owed an
issue at once, and counting them would refuse every bot run for the next
twenty minutes, those causes' own triage included. The PR burst count and the
spike limit still count them, and a cause issue is one the report's triage
files per root cause, so a loop filing them still meets both.

Also publishes ``login``/``id`` for the bot, read off the token it already
holds, so nothing downstream has to resolve them again from a configured name
a rename would leave stale.

Inputs (env): ``GITHUB_TOKEN`` (the bot's PAT, for ``gh``),
``GITHUB_REPOSITORY``, ``GITHUB_OUTPUT``, plus the run/event vars ``_issue``
reads.
"""

from __future__ import annotations

import json
import random
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from tend.runtime.shared import _common, _issue

PAUSE_LABEL = "tend-rate-limit"
PAUSE_TITLE = "Bot rate limit reached"
PAUSE_DESCRIPTION = "Bot paused on its own rate limit; close to approve"
PAUSE_COLOR = "fbca04"

CI_CAUSE_LABEL = "tend-ci-cause"
BURST_LIMIT = 10
BURST_WINDOW = timedelta(minutes=20)
BASELINE_DAYS = 6

# The jitter before the check-then-act, in seconds. It narrows the
# create-create race: sibling jobs trip within seconds of each other, and
# without it each files its own issue.
JITTER = 30

PAUSE_BODY = """\
The bot stopped before doing any work: it has filed more issues and PRs today \
than its spike limit allows, which is the check that catches a runaway loop \
between workflows.

**Closing this issue approves the volume and doubles the ceiling for the rest \
of the UTC day.** Each further close doubles it again, so the limit keeps \
working after you have used it. Close it only if the activity below is \
expected — and note the bot cannot approve itself: closes by its own account, \
or by any GitHub App, are not counted.

{row}

The runs listed above were refused and do not retry on their own; re-run them \
with `gh run rerun <id> --failed` once this is closed.
"""

_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_DAY = "%Y-%m-%d"

_COUNTS_QUERY = """\
query($recent_prs: String!, $recent_issues: String!,
      $today_posts: String!, $past_posts: String!) {
  recent_prs: search(query: $recent_prs, type: ISSUE, first: 1) { issueCount }
  recent_issues: search(query: $recent_issues, type: ISSUE, first: 1) { issueCount }
  today_posts: search(query: $today_posts, type: ISSUE, first: 1) { issueCount }
  past_posts: search(query: $past_posts, type: ISSUE, first: 1) { issueCount }
}
"""


def main() -> int:
    env = _common.require_env(
        "GITHUB_REPOSITORY",
        "GITHUB_OUTPUT",
        "GITHUB_SERVER_URL",
        "GITHUB_RUN_ID",
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
    )
    repo = env["GITHUB_REPOSITORY"]

    # Who the bot is comes from the credential, not from configuration: this
    # runs as the bot, so the authenticated user is the bot by definition. A
    # configured name could be misspelled or left stale by a rename, and every
    # way that went wrong failed open — the counts below would match nothing
    # and never trip, and the approval filter would match every close,
    # including the bot's own.
    #
    # The id is what the approval filter compares, because the bot account is
    # an ordinary user account rather than a GitHub App: the type check in
    # `count_approvals` does nothing for it, so identifying it is the whole
    # control.
    try:
        identity = _common.gh_json("api", "user")
        bot, bot_id = identity["login"], identity["id"]
        if not isinstance(bot, str) or not bot or type(bot_id) is not int:
            raise _common.UnexpectedShape("invalid authenticated identity")
    except (*_common.GH_READ_FAILED, KeyError, TypeError, ValueError):
        return _common.fail(
            "Rate limit preflight: could not read the bot's own identity from the "
            "token, so the limit cannot be enforced."
        )

    # Published for the rest of the job, which needs the same two facts.
    _common.set_output("login", bot)
    _common.set_output("id", str(bot_id))

    now = _common.utcnow()
    today = now.strftime(_DAY)
    since = (now - BURST_WINDOW).strftime(_STAMP)

    today_query = activity_query(repo, bot, today)
    first = (now - timedelta(days=BASELINE_DAYS)).strftime(_DAY)
    last = (now - timedelta(days=1)).strftime(_DAY)
    try:
        counts = _search_counts(
            {
                "recent_prs": activity_query(repo, bot, f">{since}") + " is:pr",
                "recent_issues": activity_query(repo, bot, f">{since}")
                + f" is:issue -label:{CI_CAUSE_LABEL}",
                "today_posts": today_query,
                "past_posts": activity_query(repo, bot, f"{first}..{last}"),
            }
        )
    except _common.GH_READ_FAILED as error:
        return _common.fail(
            "Rate limit preflight: activity could not be read; "
            f"no rate-limit issue was changed. {error}"
        )
    recent_prs = counts["recent_prs"]
    recent_issues = counts["recent_issues"]
    today_posts = counts["today_posts"]
    past_posts = counts["past_posts"]
    limit = spike_limit(past_posts)

    _common.log(
        "rate-limit",
        f"burst={recent_prs} PRs, {recent_issues} issues (20min); "
        f"today={today_posts} (limit: {limit}); baseline={past_posts} ({BASELINE_DAYS} days)",
    )

    abort = False
    if recent_prs > BURST_LIMIT:
        _common.annotate(
            "error",
            f"Rate limit: bot created {recent_prs} PRs in the last 20 minutes "
            f"(limit: {BURST_LIMIT})",
        )
        abort = True
    if recent_issues > BURST_LIMIT:
        _common.annotate(
            "error",
            f"Rate limit: bot created {recent_issues} issues in the last 20 minutes "
            f"(limit: {BURST_LIMIT})",
        )
        abort = True

    # A burst is not resumable; offering a ceiling increase would promise a
    # recovery that closing an issue cannot deliver.
    if abort:
        return 1

    # Everything below runs only once the base limit is already exceeded, so
    # the common path costs no extra API calls at all.
    if today_posts > limit:
        try:
            pauses = _issue.matching(PAUSE_LABEL, "all", PAUSE_TITLE)
            if not pauses:
                time.sleep(random.randrange(JITTER))
                pauses = _issue.matching(PAUSE_LABEL, "all", PAUSE_TITLE)
            approvals = sum(
                _approvals(repo, number, bot_id, today) for number in pauses
            )
        except (*_common.GH_READ_FAILED, KeyError, TypeError, ValueError) as error:
            return _common.fail(
                "Rate limit preflight: pause issues or approvals could not be read; "
                f"no rate-limit issue was changed. {error}"
            )
        pause = pauses[0] if pauses else None
        ceiling = limit << approvals

        if today_posts <= ceiling:
            _common.log(
                "rate-limit",
                f"{today_posts} today is over the base limit of {limit}, allowed by "
                f"{approvals} approval(s) (ceiling {ceiling})",
            )
        else:
            headline = (
                f"Rate limit: bot created {today_posts} items today, above the "
                f"ceiling of {ceiling} (base limit {limit}, {approvals} approval(s), "
                f"baseline {past_posts} over past {BASELINE_DAYS} days)"
            )
            activity_url = (
                f"{env['GITHUB_SERVER_URL']}/search?"
                f"{urlencode({'q': today_query, 'type': 'issues'})}"
            )
            row = (
                f"{_issue.row()}\n\n"
                f"{today_posts} items today; ceiling {ceiling} "
                f"(base {limit}, {approvals} approval(s), "
                f"{past_posts} items over the previous {BASELINE_DAYS} days).\n\n"
                f"[View items counted today ({today} UTC)]({activity_url})"
            )
            recovery = _file_pause_issue(row, pause)
            _common.annotate("error", f"{headline}. {recovery}")
            return 1
    _common.log("rate-limit", "check passed")
    return 0


def activity_query(repo: str, bot: str, created: str) -> str:
    """Issues and PRs filed by this bot in the specified creation window."""
    return f"author:{bot} repo:{repo} created:{created}"


def _search_counts(queries: dict[str, str]) -> dict[str, int]:
    """Read every count together; unavailable evidence is never a zero count."""
    try:
        result = _common.gh_json(
            "api",
            "graphql",
            "--input",
            "-",
            input=json.dumps({"query": _COUNTS_QUERY, "variables": queries}),
        )
        if not isinstance(result, dict) or result.get("errors"):
            raise _common.UnexpectedShape(f"invalid search counts: {result!r}")
        data = result.get("data")
        if not isinstance(data, dict):
            raise _common.UnexpectedShape(f"missing search counts: {result!r}")
        counts = {}
        for name in queries:
            field = data.get(name)
            if (
                not isinstance(field, dict)
                or type(field.get("issueCount")) is not int
                or field["issueCount"] < 0
            ):
                raise _common.UnexpectedShape(f"invalid search count {name}: {field!r}")
            counts[name] = field["issueCount"]
        return counts
    except _common.GH_READ_FAILED as error:
        raise _common.UnexpectedShape(f"gh api graphql: {error}") from error


def spike_limit(past_posts: int) -> int:
    """``10 + 2 * daily_avg``, i.e. ``10 + past_posts / 3`` over six days."""
    return 10 + past_posts // 3


def count_approvals(
    events: list[dict[str, Any]], *, label: str, bot_id: int, today: str
) -> int:
    """Closes by a person, after the label went on, and today.

    Today, because the ceiling it lifts resets at the UTC rollover along with
    the count itself. After the label, because otherwise moving the label onto
    an issue closed at any earlier point would import that close as an
    approval — and the bot can move labels. On the issue the preflight files
    the label goes on at creation, so this excludes nothing real.

    The two actor exclusions cover different things: the id rules out the bot,
    and ``type != "Bot"`` rules out GitHub Apps — ``github-actions[bot]``
    above all, which a workflow's own ``GITHUB_TOKEN`` acts as, and which is a
    different account that would otherwise read as a person. Excluding the
    class rather than naming apps needs no allowlist.
    """
    since = f"{today}T00:00:00Z"
    for event in events:
        # These fields decide whether a person approved more work. Missing
        # evidence must stop the check, not silently manufacture a verdict.
        datetime.strptime(event["created_at"], _STAMP)
        if event["event"] == "labeled" and not isinstance(event["label"]["name"], str):
            raise _common.UnexpectedShape("invalid event label")
        labelled = event["event"] == "labeled"
        if labelled and event["label"]["name"] == label:
            at = event["created_at"]
            since = max(since, at)
    return sum(1 for event in events if _is_approval(event, bot_id=bot_id, since=since))


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
        return f"Refused runs are listed in #{pause}; closing it doubles the ceiling."

    _issue.ensure_label(PAUSE_LABEL, PAUSE_DESCRIPTION, PAUSE_COLOR)
    # The row twice over: it seeds the body, and it goes to the reconcile as
    # the carry-over argument, so that a leg standing down to a racing sibling
    # moves the row onto the keeper before closing. Skipping it there would
    # strand the refused run's only record in a closed duplicate while the
    # `::error::` names the survivor — and the survivor is the issue whose
    # close lifts the ceiling, so the one artifact a maintainer is asked to
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
            "nothing to close and the ceiling holds until the UTC rollover."
        )
    if number is None:
        # Filed, but no number came back: `gh issue create` printed something
        # other than an issue URL. The label points at the same issue just as
        # uniquely, where naming `#` would point at nothing.
        return (
            f"Refused runs are listed in the open `{PAUSE_LABEL}` issue; closing it "
            "doubles the ceiling."
        )
    return f"Refused runs are listed in #{number}; closing it doubles the ceiling."


def _approvals(repo: str, issue: int, bot_id: int, today: str) -> int:
    """Read all approval events; an unavailable history is not zero approvals.

    /events omits comments, which accumulate on every refused run, and carries
    all facts needed to count the maintainer's closes.
    """
    events = _common.gh_paginated(f"repos/{repo}/issues/{issue}/events?per_page=100")
    return count_approvals(events, label=PAUSE_LABEL, bot_id=bot_id, today=today)
