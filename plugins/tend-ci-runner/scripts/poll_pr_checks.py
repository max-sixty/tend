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
from typing import Any

import github_cli

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"/actions/runs/(\d+)/")
RED_CONCLUSIONS = {
    "FAILURE",
    "TIMED_OUT",
    "STARTUP_FAILURE",
    "ACTION_REQUIRED",
    "ERROR",
}
GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $oid: GitObjectID!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) {
      ... on Commit {
        statusCheckRollup {
          contexts(first: 100, after: $cursor) {
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
    allow_filtered_empty: bool = False,
) -> dict[str, list[str]] | None:
    """Filter and collapse raw contexts to pending and failed check names."""
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
        if context["workflow"] == "tend-review":
            continue
        contexts.append(context)

    if not contexts:
        return {"pending": [], "failed": []} if allow_filtered_empty else None

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
    return {
        "pending": [
            context["name"] for context in current if context["status"] != "COMPLETED"
        ],
        "failed": [
            f"{context['name']} {context['url']}"
            for context in current
            if context["status"] == "COMPLETED"
            and context["conclusion"] in RED_CONCLUSIONS
        ],
    }


def fetch_rollup(
    *,
    repo: str,
    sha: str,
    run_id: str,
    workflow: str,
    allow_filtered_empty: bool = False,
) -> dict[str, list[str]] | None:
    """Fetch every rollup page; return ``None`` when no complete view exists."""
    owner, name = repo.split("/", 1)
    cursor: str | None = None
    nodes: list[dict[str, Any]] = []
    while True:
        cursor_args = (
            ["-F", "cursor=null"] if cursor is None else ["-f", f"cursor={cursor}"]
        )
        try:
            response = github_cli.json_call(
                "api",
                "graphql",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-f",
                f"oid={sha}",
                *cursor_args,
                "-f",
                f"query={GRAPHQL_QUERY}",
                quiet=True,
            )
            contexts = _dig(
                response,
                "data",
                "repository",
                "object",
                "statusCheckRollup",
                "contexts",
            )
            if not isinstance(contexts, dict) or not isinstance(
                contexts.get("nodes"), list
            ):
                return None
            nodes.extend(contexts["nodes"])
            page_info = contexts.get("pageInfo")
            if not isinstance(page_info, dict):
                return None
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                return None
        except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
            return None
    return reduce_rollup(
        nodes,
        run_id=run_id,
        workflow=workflow,
        allow_filtered_empty=allow_filtered_empty,
    )


def head_note(*, pr: str, repo: str, sha: str) -> None:
    """Report a moved branch without retargeting the commit verdict."""
    try:
        response = github_cli.json_call(
            "pr", "view", pr, "--repo", repo, "--json", "headRefOid", quiet=True
        )
        current = response.get("headRefOid") or ""
    except (subprocess.CalledProcessError, ValueError, AttributeError):
        current = ""
    if current and current != sha:
        print(
            f"note: branch advanced to {current} — the result above is still "
            f"{sha}'s, the commit this run is accountable for"
        )


def _settle(
    *, repo: str, sha: str, sleep: Callable[[float], None]
) -> tuple[bool, dict[str, list[str]] | None]:
    """Poll until nothing pends on two reads 30s apart, or the cap expires.

    Returns whether the rollup settled, and the last complete rollup read.
    """
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    workflow = os.environ.get("GITHUB_WORKFLOW", "")
    last: dict[str, list[str]] | None = None
    for _ in range(9):
        sleep(60)
        current = fetch_rollup(repo=repo, sha=sha, run_id=run_id, workflow=workflow)
        if current is None:
            continue
        last = current
        if current["pending"]:
            continue
        sleep(30)
        current = fetch_rollup(repo=repo, sha=sha, run_id=run_id, workflow=workflow)
        if current is None:
            continue
        last = current
        if not current["pending"]:
            return True, current
    return False, last


def _run_conclusion(repo: str, failure: str) -> str:
    """The conclusion of the Actions run behind a failed check, "" for a status."""
    match = RUN_ID_RE.search(failure)
    if not match:
        return ""
    view = github_cli.json_call(
        "run", "view", match.group(1), "--repo", repo, "--json", "conclusion"
    )
    return str(view["conclusion"])


def approval(pr: str, sha: str, *, sleep: Callable[[float], None] = time.sleep) -> int:
    """Decide whether one pinned PR commit's checks allow an approval.

    A failure beside checks still running is often a cancellation cascade: a
    concurrency group cancels a run, an `if: always()` merge-gate omnibus
    reports that as FAILURE rather than cancelled, and a replacement run is
    already under way. So a red with checks pending waits for them to settle,
    and a red still standing at the cap approves only when every failing check
    belongs to a cancelled Actions run.

    Whether *sha* is still the head is not judged here: the review skill posts
    every review behind `review_preflight.py post`, which refuses a moved head.
    """
    repo = os.environ["GITHUB_REPOSITORY"]
    rollup = fetch_rollup(
        repo=repo,
        sha=sha,
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        workflow=os.environ.get("GITHUB_WORKFLOW", ""),
        allow_filtered_empty=True,
    )
    if rollup is None:
        print(f"could not read a complete check rollup for {sha}", file=sys.stderr)
        return 2
    if rollup["failed"] and rollup["pending"]:
        _, rollup = _settle(repo=repo, sha=sha, sleep=sleep)
        if rollup is None:
            print(f"no complete rollup read for {sha} while waiting", file=sys.stderr)
            return 2

    failures = rollup["failed"]
    if failures and rollup["pending"]:
        failures = [f for f in failures if _run_conclusion(repo, f) != "cancelled"]
        if not failures:
            print(f"approve: every failure on {sha} is a cancelled run; unverified:")
            print(*rollup["pending"], sep="\n")
            return 0
    if failures:
        print(f"withhold: red on {sha}:")
        print(*failures, sep="\n")
        return 1
    print(f"approve: no failing check on {sha}")
    return 0


def poll(pr: str, sha: str, *, sleep: Callable[[float], None] = time.sleep) -> int:
    """Poll one pinned PR commit until its checks settle or the cap expires."""
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

    settled, last = _settle(repo=repo, sha=sha, sleep=sleep)
    if settled and last["failed"]:
        print(f"red on {sha}:")
        print(*last["failed"], sep="\n")
        head_note(pr=pr, repo=repo, sha=sha)
        return 1
    if settled:
        print(f"green: every gating check on {sha} settled green")
        head_note(pr=pr, repo=repo, sha=sha)
        return 0
    if last is None:
        print(f"no gating check settled on {sha} — UNVERIFIED, not green")
        head_note(pr=pr, repo=repo, sha=sha)
        return 2
    print(f"poll cap hit — still pending on {sha} (UNVERIFIED, not green):")
    print(*last["pending"], sep="\n")
    if last["failed"]:
        print("failures observed so far (unconfirmed while checks pend):")
        print(*last["failed"], sep="\n")
    head_note(pr=pr, repo=repo, sha=sha)
    return 3


def main(
    argv: list[str] | None = None, *, sleep: Callable[[float], None] = time.sleep
) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else ""
    args = args[1:]
    pr = args[0] if args else ""
    sha = args[1] if len(args) > 1 else ""
    if len(args) != 2 or not SHA_RE.fullmatch(sha):
        print(
            "poll_pr_checks.py: poll|approval <pr-number> <sha>; <sha> must be "
            "a full 40-char lowercase commit "
            f"OID, got '{sha}' — UNVERIFIED, not green",
            file=sys.stderr,
        )
        return 2
    if command == "approval":
        return approval(pr, sha, sleep=sleep)
    if command == "poll":
        return poll(pr, sha, sleep=sleep)
    print(f"unknown command: {command or '<none>'}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
