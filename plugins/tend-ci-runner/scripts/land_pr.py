# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Land a reviewed pull request when its CI completes, with no session waiting.

Under `merge: yolo` with the review's CI wait off, a review that has settled
everything about a head except its CI records that with ``intend``: a commit
status, :data:`poll_pr_checks.LAND_CONTEXT`, created by the bot on the exact
head it reviewed. The review then ends. ``land`` runs in the generated
tend-land workflow each time one of the repository's CI workflows completes,
and merges any open PR at that commit whose record still holds.

The judgement — human holds in prose, findings, ownership — belongs to the
review that wrote the record. ``land`` decides only what is crisp in the data,
and declines whenever a fact it reads leaves the review's judgement open:

* the record is the bot's, on this exact commit, and the latest for its
  context;
* the PR is open, not a draft, and its head is still that commit;
* on a PR the bot didn't author, the bot's approval stands on that commit;
* no person has commented or reviewed since the record, since anything they
  wrote may change what the review decided;
* every check on the commit has finished green.

A red result on the bot's own PR is handed to a session (``red_pr`` output),
which continues the repair as `/tend-ci-runner:continue-pr` describes; on
another author's PR it is left as a review that ended on pending checks
leaves it. Anything else ends without a merge, and a merge GitHub refuses
leaves the PR open.

GitHub's native auto-merge is not used: it stays enabled across pushes by
anyone with write access, so it can land a head no review read. The merge
here passes the recorded ``sha``, which GitHub refuses once the head moves.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any, Literal

import bot_review_state
import github_cli
import poll_pr_checks

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
Decision = Literal["merge", "red", "skip"]


def record(statuses: list[dict[str, Any]], bot: str) -> dict[str, Any] | None:
    """The standing landing record among a commit's statuses, newest first.

    GitHub serves statuses newest first, and the latest of a context is the
    one that stands, so a later status of the same context replaces it.
    """
    for status in statuses:
        if status.get("context") != poll_pr_checks.LAND_CONTEXT:
            continue
        if (
            status.get("state") == "success"
            and github_cli.actor_login(status.get("creator")) == bot
        ):
            return status
        return None
    return None


def people_since(
    activity: list[dict[str, Any]], *, since: str, bot: str
) -> list[str]:
    """Logins of people (not bots) who wrote on the PR after *since*.

    A bare approval says nothing the record didn't already, so it doesn't
    count; an approval with words might, so it does.
    """
    return github_cli.unique(
        github_cli.actor_login(item.get("user"))
        for item in activity
        if (item.get("created_at") or item.get("submitted_at") or "") > since
        and isinstance(item.get("user"), dict)
        and item["user"].get("type") == "User"
        and github_cli.actor_login(item.get("user")) != bot
        and not (item.get("state") == "APPROVED" and not item.get("body"))
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
    if pr.get("draft"):
        return "skip", f"#{number}: draft"
    own = github_cli.actor_login(pr.get("user")) == bot
    if not own and approval_sha != sha:
        return "skip", f"#{number}: the bot's approval no longer stands on {sha}"
    if people:
        return "skip", (
            f"#{number}: {', '.join(people)} wrote since the landing was recorded"
        )
    if rollup is None:
        return "skip", f"#{number}: no complete check rollup for {sha}"
    if rollup["pending"]:
        return "skip", f"#{number}: still running: {', '.join(rollup['pending'])}"
    if rollup["failed"]:
        # Each failing workflow's completion reaches here; only a red
        # completion hands on, so a green sibling finishing later doesn't
        # start a second session for the same failure.
        if own and run_conclusion == "failure":
            return "red", f"#{number}: red on {sha}: {', '.join(rollup['failed'])}"
        return "skip", f"#{number}: red on {sha}: {', '.join(rollup['failed'])}"
    if rollup["unverified"]:
        return "skip", (
            f"#{number}: settled without a result: {', '.join(rollup['unverified'])}"
        )
    return "merge", f"#{number}: every check on {sha} settled green"


def intend(pr: str, sha: str) -> int:
    """Record that the review settled *sha* and it lands once CI is green."""
    repo = github_cli.repository()
    head = github_cli.json_call(
        "pr", "view", pr, "--repo", repo, "--json", "headRefOid,state"
    )
    if head["state"] != "OPEN" or head["headRefOid"] != sha:
        print(f"PR #{pr} is not open at {sha}; no landing recorded", file=sys.stderr)
        return 1
    fields = [
        "-f",
        "state=success",
        "-f",
        f"context={poll_pr_checks.LAND_CONTEXT}",
        "-f",
        "description=Reviewed; lands when its checks pass",
    ]
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        fields += ["-f", f"target_url={server}/{repo}/actions/runs/{run_id}"]
    github_cli.run("api", f"repos/{repo}/statuses/{sha}", "-X", "POST", *fields)
    print(f"landing recorded on {sha}: tend-land merges PR #{pr} once CI is green")
    return 0


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
    landing = record(statuses, bot)
    red: list[int] = []
    for pr in pulls:
        number = pr["number"]
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
                repo=repo, sha=sha, run_id="", workflow="", allow_filtered_empty=True
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
    if len(args) == 3 and args[0] == "intend" and args[1].isdigit():
        if SHA_RE.fullmatch(args[2]):
            return intend(args[1], args[2])
    if len(args) == 3 and args[0] == "land" and SHA_RE.fullmatch(args[1]):
        return land(args[1], args[2])
    print(
        f"usage: {sys.argv[0]} intend <pr-number> <sha> | land <sha> <conclusion>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
