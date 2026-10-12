# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Hand a reviewed PR's landing to the completion of its CI.

Under `merge: yolo` with the review's CI wait off, no session waits ~an hour
for a head's CI to land it. Three records, each written by the session that
knows its fact, carry the landing across instead:

* ``pushed <sha>`` — a session of this bot that pushes a PR head says so, as
  a ``tend/land/pushed`` status on that commit. Only the bot's token can create a
  status the bot created, so a head without one was not the bot's push. A
  missing record errs safe: that head's landing stays with a maintainer.
* ``intend <pr> <sha> <base>`` — the review that settled everything about a
  head the bot pushed, except its CI, records the head for landing: a
  ``tend/land/<pr>`` status whose description names the base the review
  judged and the checks its CI policy skips. It refuses a head with no
  ``tend/land/pushed`` record, or whose PR has since moved off that head or base.
* ``check <sha> <conclusion>`` — tend-land runs this, with no agent, each
  time something that reports on a commit finishes: any of the repository's
  workflows, a commit status, or another app's check suite, so whatever the
  gate below waits on also wakes it. It decides only what is crisp: whether a PR at that commit holds a landing record for its head
  and base, and whether every check but the recorded skips finished green.
  Green with a record starts a short landing session, which makes the
  landing decision per `/tend-ci-runner:merge-pr` — holds, findings,
  approval, ownership are judgement, read there, not here. A red on a PR with
  no human author starts a session that continues its repair per
  `/tend-ci-runner:continue-pr`, record or not, as the review that waited
  used to.

GitHub's native auto-merge is not used: it stays enabled across pushes by
anyone with write access, so it can land a head no review read. The landing
session merges with the recorded ``sha``, which GitHub refuses once the head
moves.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Literal

import github_cli
import poll_pr_checks

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PUSHED_CONTEXT = f"{poll_pr_checks.LAND_CONTEXT}/pushed"
#: GitHub truncates a longer status description, which would lose the policy.
DESCRIPTION_LIMIT = 140
Decision = Literal["land", "red", "skip"]


def context(number: int | str) -> str:
    """The status context recording PR *number*'s landing.

    One per PR, so a commit that heads two PRs carries a separate record for
    each, and the review of one never lands the other.
    """
    return f"{poll_pr_checks.LAND_CONTEXT}/{number}"


def _standing(
    statuses: list[dict[str, Any]], name: str, bot: str
) -> dict[str, Any] | None:
    """The bot's standing success status of context *name*, newest first.

    GitHub serves statuses newest first, and the latest of a context is the
    one that stands, so a later status of the same context replaces it.
    """
    for status in statuses:
        if status.get("context") != name:
            continue
        if (
            status.get("state") == "success"
            and github_cli.actor_login(status.get("creator")) == bot
        ):
            return status
        return None
    return None


def record(
    statuses: list[dict[str, Any]], bot: str, number: int
) -> dict[str, Any] | None:
    """PR *number*'s landing record, with its ``base`` and ``skip``."""
    status = _standing(statuses, context(number), bot)
    if status is None:
        return None
    try:
        policy = json.loads(status.get("description") or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(policy, dict) or not isinstance(policy.get("base"), str):
        return None
    skip = policy.get("skip", [])
    if not isinstance(skip, list) or not all(isinstance(n, str) for n in skip):
        return None
    return {**status, "base": policy["base"], "skip": frozenset(skip)}


def no_human_author(pr: dict[str, Any], bot: str) -> bool:
    """True for this bot's PR or another bot's (Dependabot, Renovate)."""
    user = pr.get("user")
    return github_cli.actor_login(user) == bot or (
        isinstance(user, dict) and user.get("type") == "Bot"
    )


def decide(
    *,
    pr: dict[str, Any],
    sha: str,
    bot: str,
    landing: dict[str, Any] | None,
    rollup: dict[str, list[str]] | None,
    run_conclusion: str,
) -> tuple[Decision, str]:
    """Whether CI on *sha* starts PR *pr*'s landing or repair, or neither."""
    number = pr["number"]
    if pr["head"]["sha"] != sha:
        return "skip", f"#{number}: no longer at {sha}"
    if landing is not None and pr["base"]["ref"] != landing["base"]:
        landing = None  # retargeted since the review: the record no longer holds
    if rollup is None:
        return "skip", f"#{number}: no complete check rollup for {sha}"
    if rollup["failed"]:
        # A failed check is final, so the run that failed hands it on even
        # while siblings still run. A sibling that completes green later
        # reads the same failure and hands nothing on; one that fails too
        # queues behind the first session (tend-land's per-PR group).
        failed = f"#{number}: red on {sha}: {', '.join(rollup['failed'])}"
        red_run = run_conclusion.upper() in poll_pr_checks.RED_CONCLUSIONS
        if no_human_author(pr, bot) and red_run:
            return "red", failed
        return "skip", failed
    if landing is None:
        return "skip", f"#{number}: no landing recorded for {sha} on its base"
    if rollup["pending"]:
        return "skip", f"#{number}: still running: {', '.join(rollup['pending'])}"
    if rollup["unverified"]:
        return "skip", (
            f"#{number}: settled without a result: {', '.join(rollup['unverified'])}"
        )
    return "land", f"#{number}: every check on {sha} settled green"


def _status(repo: str, sha: str, name: str, description: str) -> None:
    fields = [
        "-f",
        "state=success",
        "-f",
        f"context={name}",
        "-f",
        f"description={description}",
    ]
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        fields += ["-f", f"target_url={server}/{repo}/actions/runs/{run_id}"]
    github_cli.run("api", f"repos/{repo}/statuses/{sha}", "-X", "POST", *fields)


def pushed(sha: str) -> int:
    """Record that this bot pushed *sha*, so its review may record a landing."""
    repo = github_cli.repository()
    _status(repo, sha, PUSHED_CONTEXT, "Pushed by this bot")
    print(f"recorded {sha} as this bot's push")
    return 0


def intend(pr: str, sha: str, base: str, *, skip: frozenset[str] = frozenset()) -> int:
    """Record that the review settled *sha* on *base* and it lands once green.

    Exit 3 records nothing and leaves the landing to a maintainer: the head
    isn't the bot's push, the PR moved off *sha* or *base*, or *skip* doesn't
    fit the record.

    The record is itself a commit status, so creating it wakes tend-land like
    any other: a check that finished between the review's read and the record
    is not lost, since the record's own event reads every check again.
    """
    repo = github_cli.repository()
    bot = str(github_cli.json_call("api", "user")["login"])
    view = github_cli.json_call(
        "pr", "view", pr, "--repo", repo, "--json", "headRefOid,state,baseRefName"
    )
    if (
        view["state"] != "OPEN"
        or view["headRefOid"] != sha
        or view["baseRefName"] != base
    ):
        print(
            f"PR #{pr} is no longer open at {sha} on {base}; no landing recorded",
            file=sys.stderr,
        )
        return 3
    statuses = github_cli.paginated(f"repos/{repo}/commits/{sha}/statuses")
    if _standing(statuses, PUSHED_CONTEXT, bot) is None:
        print(
            f"{sha} carries no record of this bot's push; "
            "its landing stays with a maintainer",
            file=sys.stderr,
        )
        return 3
    description = json.dumps(
        {"base": base, "skip": sorted(skip)}, separators=(",", ":")
    )
    if len(description) > DESCRIPTION_LIMIT:
        print(
            "the skipped checks don't fit in a landing record; "
            "its landing stays with a maintainer",
            file=sys.stderr,
        )
        return 3
    _status(repo, sha, context(pr), description)
    print(
        f"landing recorded on {sha}: tend-land starts the landing of PR #{pr} "
        "once every check is green"
    )
    return 0


def check(sha: str, run_conclusion: str) -> int:
    """Name every PR at *sha* whose landing or repair this completion starts."""
    repo = os.environ["GITHUB_REPOSITORY"]
    bot = str(github_cli.json_call("api", "user")["login"])
    pulls = [
        pr
        for pr in github_cli.paginated(f"repos/{repo}/pulls?state=open&per_page=100")
        if pr["head"]["sha"] == sha
    ]
    if not pulls:
        print(f"no open PR at {sha}")
        return 0
    statuses = github_cli.paginated(f"repos/{repo}/commits/{sha}/statuses")
    started: dict[str, list[int]] = {"land": [], "red": []}
    for pr in pulls:
        landing = record(statuses, bot, pr["number"])
        rollup = poll_pr_checks.fetch_rollup(
            repo=repo,
            sha=sha,
            run_id="",
            workflow="",
            skip=landing["skip"] if landing else frozenset(),
            allow_filtered_empty=True,
        )
        decision, reason = decide(
            pr=pr,
            sha=sha,
            bot=bot,
            landing=landing,
            rollup=rollup,
            run_conclusion=run_conclusion,
        )
        print(f"{decision}: {reason}")
        if decision != "skip":
            started[decision].append(pr["number"])
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        # Every eligible PR, for the workflow's per-PR matrix.
        with open(output, "a", encoding="utf-8") as handle:
            handle.writelines(
                f"{key}_prs={json.dumps(numbers)}\n" for key, numbers in started.items()
            )
    return 0


USAGE = (
    "usage: land_pr.py pushed <sha> | "
    "intend <pr-number> <sha> <base> [--skip <check>]... | "
    "check <sha> <conclusion>"
)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    skip: set[str] = set()
    while len(args) > 4 and args[-2] == "--skip":
        skip.add(args[-1])
        args = args[:-2]
    command = args[0] if args else ""
    if command == "pushed" and len(args) == 2 and SHA_RE.fullmatch(args[1]):
        return pushed(args[1])
    if (
        command == "intend"
        and len(args) == 4
        and args[1].isdigit()
        and SHA_RE.fullmatch(args[2])
        and args[3]
    ):
        return intend(args[1], args[2], args[3], skip=frozenset(skip))
    if command == "check" and len(args) == 3 and SHA_RE.fullmatch(args[1]):
        return check(args[1], args[2])
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
