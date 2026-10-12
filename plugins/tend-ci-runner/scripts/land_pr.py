# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Land a reviewed pull request when its CI completes, with no session waiting.

Under `merge: yolo` with the review's CI wait off, a review that has settled
everything about a head this bot pushed, except its CI, records that with
``intend`` and ends. The record is a commit status the bot creates on the
exact head, under a context naming the PR (:func:`context`), whose description
carries the base the review judged and the checks its CI policy leaves out.
``land`` runs in the generated tend-land workflow each time one of the
repository's CI workflows completes, and merges any open PR at that commit
whose record still holds.

A head someone else pushed is not recorded: a review with its CI wait off
leaves those for a maintainer, and ``intend`` refuses a head whose committer
isn't the bot.

The judgement — holds in prose, findings, ownership — belongs to the review
that wrote the record. ``land`` reads no prose. It decides only what is crisp
in the data, and declines whenever something changed after the record that
the review's judgement didn't cover:

* the record is the bot's, the latest for this PR's context, on this commit;
* the PR is open, not a draft, at that head, on the base the record names;
* on a PR the bot didn't author, the bot's approval stands on that commit;
* no person has written, edited, or reviewed on the PR at or after the
  record's time (a bare approval excepted);
* every check on the commit, less those the record leaves out, finished green.

A red result on a PR with no human author (this bot's, or another bot's such
as Dependabot's) is handed to a session (``red_pr`` output), which continues
the repair as `/tend-ci-runner:continue-pr` describes, as the review that
waited used to. On a human's PR it stands beside the approval for its author.
Anything else ends without a merge, and a merge GitHub refuses leaves the PR
open.

GitHub's native auto-merge is not used: it stays enabled across pushes by
anyone with write access, so it can land a head no review read. The merge
here passes the recorded ``sha``, which GitHub refuses once the head moves.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Literal

import bot_review_state
import github_cli
import poll_pr_checks

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: GitHub truncates a longer status description, which would lose the policy.
DESCRIPTION_LIMIT = 140
Decision = Literal["merge", "red", "skip"]


def context(number: int | str) -> str:
    """The status context recording PR *number*'s landing.

    One per PR, so a commit that heads two PRs carries a separate record for
    each, and the review of one never lands the other.
    """
    return f"{poll_pr_checks.LAND_CONTEXT}/{number}"


def record(
    statuses: list[dict[str, Any]], bot: str, number: int
) -> dict[str, Any] | None:
    """PR *number*'s standing landing record among a commit's statuses.

    GitHub serves statuses newest first, and the latest of a context is the
    one that stands, so a later status of the same context replaces it. The
    returned record carries the description's ``base`` and ``skip``.
    """
    for status in statuses:
        if status.get("context") != context(number):
            continue
        if (
            status.get("state") != "success"
            or github_cli.actor_login(status.get("creator")) != bot
        ):
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
    return None


def _when(item: dict[str, Any]) -> str:
    return max(
        str(item.get(key) or "") for key in ("created_at", "updated_at", "submitted_at")
    )


def people_since(activity: list[dict[str, Any]], *, since: str, bot: str) -> list[str]:
    """Logins of people (not bots) who wrote on the PR at or after *since*.

    An edit counts from when it was made, so an earlier comment rewritten
    after the record counts; a tie counts, since the order within GitHub's
    one-second timestamps is unknown. A bare approval says nothing the record
    didn't already, so it doesn't count; an approval with words might.
    """
    return github_cli.unique(
        github_cli.actor_login(item.get("user"))
        for item in activity
        if _when(item) >= since
        and isinstance(item.get("user"), dict)
        and item["user"].get("type") == "User"
        and github_cli.actor_login(item.get("user")) != bot
        and not (item.get("state") == "APPROVED" and not item.get("body"))
    )


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
    approval_sha: str,
    people: list[str],
    rollup: dict[str, list[str]] | None,
    run_conclusion: str,
) -> tuple[Decision, str]:
    """Whether to merge PR *pr* at *sha*, hand its red CI on, or leave it."""
    number = pr["number"]
    if landing is None:
        return "skip", f"#{number}: no landing recorded on {sha}"
    if pr.get("state") != "open" or pr["head"]["sha"] != sha:
        return "skip", f"#{number}: no longer open at {sha}"
    if pr["base"]["ref"] != landing["base"]:
        return "skip", (
            f"#{number}: retargeted to {pr['base']['ref']} since the review of "
            f"{landing['base']}"
        )
    if pr.get("draft"):
        return "skip", f"#{number}: draft"
    if rollup is None:
        return "skip", f"#{number}: no complete check rollup for {sha}"
    if rollup["failed"]:
        # A failed check is final, so the run that failed hands it on even
        # while siblings still run. A sibling that completes green later
        # reads the same failure and hands nothing on; one that fails too
        # queues behind the first session (tend-land's per-PR `red` group).
        failed = f"#{number}: red on {sha}: {', '.join(rollup['failed'])}"
        if no_human_author(pr, bot) and run_conclusion == "failure":
            return "red", failed
        return "skip", failed
    own = github_cli.actor_login(pr.get("user")) == bot
    if not own and approval_sha != sha:
        return "skip", f"#{number}: the bot's approval no longer stands on {sha}"
    if people:
        return "skip", (
            f"#{number}: {', '.join(people)} wrote since the landing was recorded"
        )
    if rollup["pending"]:
        return "skip", f"#{number}: still running: {', '.join(rollup['pending'])}"
    if rollup["unverified"]:
        return "skip", (
            f"#{number}: settled without a result: {', '.join(rollup['unverified'])}"
        )
    return "merge", f"#{number}: every check on {sha} settled green"


def intend(pr: str, sha: str, *, skip: frozenset[str] = frozenset()) -> int:
    """Record that the review settled *sha* and it lands once CI is green.

    Only a head this bot pushed is recorded (exit 3 otherwise, leaving the
    landing to a maintainer). The record names the PR's base and *skip*, the
    checks the review's CI policy leaves out, so the lander judges the same
    checks against the same base.

    tend-land acts on a CI completion, so a completion that arrived between
    the review's read and this record found nothing to land and fires no
    more. Reading CI again once the record stands closes that gap: with
    checks still running, a later completion will find the record (exit 2,
    as the review's own read); settled, the review handles the result itself
    as it would a settled read (exit 0 green, 1 red).
    """
    repo = github_cli.repository()
    bot = str(github_cli.json_call("api", "user")["login"])
    view = github_cli.json_call(
        "pr", "view", pr, "--repo", repo, "--json", "headRefOid,state,baseRefName"
    )
    if view["state"] != "OPEN" or view["headRefOid"] != sha:
        print(f"PR #{pr} is not open at {sha}; no landing recorded", file=sys.stderr)
        return 1
    commit = github_cli.json_call("api", f"repos/{repo}/commits/{sha}")
    if github_cli.actor_login(commit.get("committer")) != bot:
        print(
            f"{sha} is not this bot's push; its landing stays with a maintainer",
            file=sys.stderr,
        )
        return 3
    description = json.dumps(
        {"base": view["baseRefName"], "skip": sorted(skip)}, separators=(",", ":")
    )
    if len(description) > DESCRIPTION_LIMIT:
        print(
            "the skipped checks don't fit in a landing record; "
            "its landing stays with a maintainer",
            file=sys.stderr,
        )
        return 3
    fields = [
        "-f",
        "state=success",
        "-f",
        f"context={context(pr)}",
        "-f",
        f"description={description}",
    ]
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        fields += ["-f", f"target_url={server}/{repo}/actions/runs/{run_id}"]
    github_cli.run("api", f"repos/{repo}/statuses/{sha}", "-X", "POST", *fields)
    print(f"landing recorded on {sha}")
    code = poll_pr_checks.poll(pr, sha, skip=skip, wait=False)
    if code == 2:
        print(f"tend-land merges PR #{pr} once CI is green")
    else:
        print("CI settled since the review's read: handle this result now")
    return code


def _activity(repo: str, number: int) -> list[dict[str, Any]]:
    return [
        *github_cli.paginated(f"repos/{repo}/issues/{number}/comments"),
        *github_cli.paginated(f"repos/{repo}/pulls/{number}/reviews"),
        *github_cli.paginated(f"repos/{repo}/pulls/{number}/comments"),
    ]


def land(sha: str, run_conclusion: str) -> int:
    """Merge or hand on each open PR at *sha* whose landing record holds."""
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
    red: list[int] = []
    for pr in pulls:
        number = pr["number"]
        landing = record(statuses, bot, number)
        approval_sha = ""
        people: list[str] = []
        rollup = None
        if landing is not None:
            if github_cli.actor_login(pr.get("user")) != bot:
                approval_sha = bot_review_state.fetch_review_state(
                    str(number), repo=repo
                )["fresh_approval_sha"]
            people = people_since(
                _activity(repo, number), since=landing["created_at"], bot=bot
            )
            rollup = poll_pr_checks.fetch_rollup(
                repo=repo,
                sha=sha,
                run_id="",
                workflow="",
                skip=landing["skip"],
                allow_filtered_empty=True,
            )
        decision, reason = decide(
            pr=pr,
            sha=sha,
            bot=bot,
            landing=landing,
            approval_sha=approval_sha,
            people=people,
            rollup=rollup,
            run_conclusion=run_conclusion,
        )
        print(f"{decision}: {reason}")
        if decision == "red":
            red.append(number)
        if decision != "merge":
            continue
        try:
            github_cli.run(
                "api",
                f"repos/{repo}/pulls/{number}/merge",
                "-X",
                "PUT",
                "-f",
                f"sha={sha}",
                "-f",
                "merge_method=squash",
            )
        except subprocess.CalledProcessError as error:
            # 409: the head moved off `sha`; 405: closed or not mergeable.
            # Either way GitHub's refusal is the verdict, and the PR stays open.
            print(f"#{number}: GitHub refused the merge: {error.stderr.strip()}")
            continue
        print(f"#{number}: merged at {sha}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output and red:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"red_pr={red[0]}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    skip: set[str] = set()
    while len(args) > 3 and args[-2] == "--skip":
        skip.add(args[-1])
        args = args[:-2]
    if (
        len(args) == 3
        and args[0] == "intend"
        and args[1].isdigit()
        and SHA_RE.fullmatch(args[2])
    ):
        return intend(args[1], args[2], skip=frozenset(skip))
    if len(args) == 3 and args[0] == "land" and SHA_RE.fullmatch(args[1]):
        return land(args[1], args[2])
    print(
        f"usage: {sys.argv[0]} intend <pr-number> <sha> [--skip <check>]... | "
        "land <sha> <conclusion>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
