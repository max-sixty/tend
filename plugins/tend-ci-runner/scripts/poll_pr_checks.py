# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Poll one commit's status-check rollup to a fail-closed verdict."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any, Literal

import github_cli

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RED_CONCLUSIONS = {
    "FAILURE",
    "TIMED_OUT",
    "STARTUP_FAILURE",
    "ACTION_REQUIRED",
    "ERROR",
}
# The conclusions that pass. Anything else terminal and not red produced no
# result — CANCELLED or STALE, a COMPLETED check carrying no conclusion, or a
# conclusion GitHub adds later — so it is neither red nor green. Naming green
# rather than the no-result set keeps the unrecognized case fail-closed.
GREEN_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
#: Seconds between rollup reads, and between a clean read and the one that
#: confirms it.
POLL_SEC = 60
CONFIRM_SEC = 30
#: How long a commit may show no check before it counts as having none. A
#: check registers when its run is queued, seconds after the push, so this is
#: a margin for GitHub's lag and a slow external status, not for CI to run.
REGISTRATION_SEC = 5 * 60
GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $oid: GitObjectID!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) {
      ... on Commit {
        statusCheckRollup {
          contexts(first: 100, after: $endCursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              __typename
              ... on CheckRun {
                name status conclusion startedAt detailsUrl
                checkSuite { workflowRun { workflow { name } } }
              }
              ... on StatusContext { context state targetUrl }
            }
          }
        }
      }
    }
  }
}
"""


def _dig(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def reduce_rollup(
    nodes: list[dict[str, Any]],
    *,
    run_id: str,
    workflow: str,
    skip: frozenset[str] = frozenset(),
    allow_filtered_empty: bool = False,
) -> dict[str, list[str]] | None:
    """Filter and collapse raw contexts to pending, failed and unverified names.

    Drops this run's own checks, tend-review's, and any named in *skip*.
    """
    contexts: list[dict[str, str]] = []
    own_run = f"/runs/{run_id}/" if run_id else ""
    for node in nodes:
        if node.get("__typename") == "CheckRun":
            context = {
                "name": str(node.get("name") or ""),
                "status": str(node.get("status") or ""),
                "conclusion": str(node.get("conclusion") or ""),
                "workflow": str(
                    _dig(node, "checkSuite", "workflowRun", "workflow", "name") or ""
                ),
                "url": str(node.get("detailsUrl") or ""),
                "started_at": str(node.get("startedAt") or ""),
            }
        else:
            state = str(node.get("state") or "")
            context = {
                "name": str(node.get("context") or ""),
                "status": (
                    "PENDING" if state in {"PENDING", "EXPECTED"} else "COMPLETED"
                ),
                "conclusion": state,
                "workflow": "",
                "url": str(node.get("targetUrl") or ""),
                "started_at": "",
            }
        if own_run and own_run in context["url"]:
            continue
        if workflow and context["workflow"] == workflow:
            continue
        if context["workflow"] == "tend-review" or context["name"] in skip:
            continue
        contexts.append(context)

    if not contexts:
        return (
            {"pending": [], "failed": [], "unverified": []}
            if allow_filtered_empty
            else None
        )

    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for context in contexts:
        groups.setdefault((context["name"], context["workflow"]), []).append(context)

    current: list[dict[str, str]] = []
    for group in groups.values():
        pending = [context for context in group if context["status"] != "COMPLETED"]
        current.append(
            pending[0]
            if pending
            else max(group, key=lambda context: context["started_at"])
        )
    # A job held by an environment's protection rules moves only when someone
    # outside the run approves it, so it has settled without a result rather
    # than still pending.
    settled = {"COMPLETED", "WAITING"}
    return {
        "pending": [
            context["name"] for context in current if context["status"] not in settled
        ],
        "failed": [
            f"{context['name']} {context['url']}"
            for context in current
            if context["status"] == "COMPLETED"
            and context["conclusion"] in RED_CONCLUSIONS
        ],
        "unverified": [
            f"{context['name']} {context['url']}"
            for context in current
            if context["status"] == "WAITING"
            or context["status"] == "COMPLETED"
            and context["conclusion"] not in (RED_CONCLUSIONS | GREEN_CONCLUSIONS)
        ],
    }


def fetch_rollup(
    *,
    repo: str,
    sha: str,
    run_id: str,
    workflow: str,
    skip: frozenset[str] = frozenset(),
    allow_filtered_empty: bool = False,
) -> dict[str, list[str]] | None:
    """Fetch every rollup page; return ``None`` when no complete view exists."""
    owner, name = repo.split("/", 1)
    try:
        pages = github_cli.pages(
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-f",
            f"oid={sha}",
            "-f",
            f"query={GRAPHQL_QUERY}",
            quiet=True,
        )
    except (subprocess.CalledProcessError, ValueError):
        return None
    if not isinstance(pages, list) or not pages:
        return None
    nodes: list[dict[str, Any]] = []
    for page in pages:
        contexts = _dig(
            page, "data", "repository", "object", "statusCheckRollup", "contexts"
        )
        if not isinstance(contexts, dict) or not isinstance(
            contexts.get("nodes"), list
        ):
            return None
        nodes.extend(contexts["nodes"])
    # `gh` stops at a page that reports more but names no cursor to follow.
    if _dig(contexts, "pageInfo", "hasNextPage"):
        return None
    return reduce_rollup(
        nodes,
        run_id=run_id,
        workflow=workflow,
        skip=skip,
        allow_filtered_empty=allow_filtered_empty,
    )


def _head(*, pr: str, repo: str) -> str:
    """The PR's current head, or "" when it can't be read."""
    try:
        response = github_cli.json_call(
            "pr", "view", pr, "--repo", repo, "--json", "headRefOid", quiet=True
        )
        return response.get("headRefOid") or ""
    except (subprocess.CalledProcessError, ValueError, AttributeError):
        return ""


def head_note(*, pr: str, repo: str, sha: str) -> None:
    """Report a moved branch without retargeting the commit verdict."""
    current = _head(pr=pr, repo=repo)
    if current and current != sha:
        print(
            f"note: branch advanced to {current} — the result above is still "
            f"{sha}'s, the commit this run is accountable for"
        )


def _settle(
    *,
    repo: str,
    pr: str,
    sha: str,
    skip: frozenset[str] = frozenset(),
    sleep: Callable[[float], None],
) -> tuple[Literal["settled", "moved", "none"], dict[str, list[str]] | None]:
    """Poll until nothing pends on two reads 30s apart.

    Returns how the wait ended, and the last complete rollup read. The wait has
    no time bound: a running check ends by its own job's timeout, so the wait
    ends with the checks however long the consumer's CI takes. Two states have
    no such end, and each is ended by the event that shows it:

    * checks still pend and the PR's head has moved off *sha* ("moved"). A
      merge requires *sha* to be the head, and the new head is its pusher's to
      verify — a review of it is already queued behind this session.
    * no complete rollup for :data:`REGISTRATION_SEC` ("none"): the commit
      has no check coming, or GitHub isn't answering for it.

    A check that registers and never finishes — a status its app never
    reports — holds the wait until the session's own timeout ends the run,
    which then reads as timed out. A job waiting on an environment approval
    doesn't: :func:`reduce_rollup` counts it as settled without a result.
    """
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    workflow = os.environ.get("GITHUB_WORKFLOW", "")
    last: dict[str, list[str]] | None = None
    waited = 0
    while True:
        sleep(POLL_SEC)
        waited += POLL_SEC
        current = fetch_rollup(
            repo=repo, sha=sha, run_id=run_id, workflow=workflow, skip=skip
        )
        if current is None:
            if last is None and waited >= REGISTRATION_SEC:
                return "none", None
            continue
        last = current
        if current["pending"]:
            if _head(pr=pr, repo=repo) not in {"", sha}:
                return "moved", last
            continue
        sleep(CONFIRM_SEC)
        current = fetch_rollup(
            repo=repo, sha=sha, run_id=run_id, workflow=workflow, skip=skip
        )
        if current is None:
            continue
        last = current
        if not current["pending"]:
            return "settled", current


def approval(
    pr: str,
    sha: str,
    *,
    skip: frozenset[str] = frozenset(),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Decide whether one pinned PR commit's checks allow an approval.

    A failure beside checks still running is often a cancellation cascade: a
    concurrency group cancels a run, an `if: always()` merge-gate omnibus
    reports that as FAILURE rather than cancelled, and a replacement run is
    already under way. So a red with checks pending waits for them to settle,
    where the replacement's result supersedes the cancelled one's.

    A check that settled without a result — cancelled, stale, held for an
    environment's approval, or a conclusion outside the passing set — never
    reached a verdict, so it cannot withhold on its merits. It approves, named
    as unverified so the approval doesn't read as a check that passed.

    A head that moves during that wait leaves the approval undecided; otherwise
    whether *sha* is still the head is not judged here: the review skill posts
    every review behind `review_preflight.py post`, which refuses a moved head.
    """
    repo = os.environ["GITHUB_REPOSITORY"]
    rollup = fetch_rollup(
        repo=repo,
        sha=sha,
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        workflow=os.environ.get("GITHUB_WORKFLOW", ""),
        skip=skip,
        allow_filtered_empty=True,
    )
    if rollup is None:
        print(f"could not read a complete check rollup for {sha}", file=sys.stderr)
        return 2
    if rollup["failed"] and rollup["pending"]:
        outcome, rollup = _settle(repo=repo, pr=pr, sha=sha, skip=skip, sleep=sleep)
        if outcome == "moved":
            print(f"PR head moved off {sha} while its checks pend", file=sys.stderr)
            return 2
        if rollup is None:
            print(f"no complete rollup read for {sha} while waiting", file=sys.stderr)
            return 2

    if rollup["failed"]:
        print(f"withhold: red on {sha}:")
        print(*rollup["failed"], sep="\n")
        return 1
    if rollup["unverified"]:
        print(f"approve: no failing check on {sha}; unverified:")
        print(*rollup["unverified"], sep="\n")
        return 0
    print(f"approve: no failing check on {sha}")
    return 0


def _unverified_note(rollup: dict[str, list[str]]) -> None:
    """Name checks that settled without a result, beside another verdict."""
    if rollup["unverified"]:
        print(
            "settled without a result "
            "(cancelled, stale, awaiting approval, or unrecognized):"
        )
        print(*rollup["unverified"], sep="\n")


def poll(
    pr: str,
    sha: str,
    *,
    skip: frozenset[str] = frozenset(),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Poll one pinned PR commit until its checks, less those in *skip*, settle."""
    repo = os.environ["GITHUB_REPOSITORY"]
    try:
        github_cli.run("api", f"repos/{repo}/commits/{sha}", "--silent", quiet=True)
    except subprocess.CalledProcessError:
        sleep(10)
        try:
            github_cli.run("api", f"repos/{repo}/commits/{sha}", "--silent", quiet=True)
        except subprocess.CalledProcessError:
            print(
                f"could not resolve {sha} as a commit in {repo} — UNVERIFIED, not green"
            )
            return 2

    outcome, last = _settle(repo=repo, pr=pr, sha=sha, skip=skip, sleep=sleep)
    if outcome == "none" or last is None:
        print(
            f"no check registered on {sha} within {REGISTRATION_SEC // 60} minutes "
            "— UNVERIFIED, not green"
        )
        head_note(pr=pr, repo=repo, sha=sha)
        return 2
    settled = outcome == "settled"
    if settled and last["failed"]:
        print(f"red on {sha}:")
        print(*last["failed"], sep="\n")
        _unverified_note(last)
        head_note(pr=pr, repo=repo, sha=sha)
        return 1
    if settled and last["unverified"]:
        print(f"no result from these checks on {sha} — UNVERIFIED, not green:")
        print(*last["unverified"], sep="\n")
        head_note(pr=pr, repo=repo, sha=sha)
        return 2
    if settled:
        print(f"green: every gating check on {sha} settled green")
        head_note(pr=pr, repo=repo, sha=sha)
        return 0
    print(f"PR head moved off {sha} — still pending (UNVERIFIED, not green):")
    print(*last["pending"], sep="\n")
    if last["failed"]:
        print("failures observed so far (unconfirmed while checks pend):")
        print(*last["failed"], sep="\n")
    _unverified_note(last)
    head_note(pr=pr, repo=repo, sha=sha)
    return 3


VERDICTS = {
    "poll": {
        0: "GREEN",
        1: "RED",
        2: "UNVERIFIED, not green",
        3: "still pending when the PR head moved — UNVERIFIED, not green",
    },
    "approval": {0: "approve", 1: "withhold", 2: "undecided — do not approve"},
}


def main(
    argv: list[str] | None = None, *, sleep: Callable[[float], None] = time.sleep
) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else ""
    args = args[1:]
    skip: set[str] = set()
    while len(args) > 3 and args[-2] == "--skip":
        skip.add(args.pop())
        args.pop()
    pr = args[0] if args else ""
    sha = args[1] if len(args) > 1 else ""
    if len(args) != 2:
        print(
            "usage: poll_pr_checks.py poll|approval <pr-number> <sha> "
            "[--skip <check>]... — UNVERIFIED, not green",
            file=sys.stderr,
        )
        return 2
    if not SHA_RE.fullmatch(sha):
        print(
            "poll_pr_checks.py: <sha> must be a full 40-char lowercase commit OID, "
            f"got '{sha}' — UNVERIFIED, not green",
            file=sys.stderr,
        )
        return 2
    if command not in VERDICTS:
        print(f"unknown command: {command or '<none>'}", file=sys.stderr)
        return 2
    if command == "approval":
        code = approval(pr, sha, skip=frozenset(skip), sleep=sleep)
    else:
        code = poll(pr, sha, skip=frozenset(skip), sleep=sleep)
    # Sessions pipe this through `tail -N`, which drops the leading verdict
    # behind a long check list, so the last line restates it.
    print(f"verdict: {VERDICTS[command][code]} on {sha} (exit {code})")
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
