"""List the open issues where the bot had the last word and nothing will act next.

A comment from anyone but the bot starts an event workflow, and the
notifications poll recovers what those runs miss. The bot gets no event for
its own writes, so an issue it last wrote on waits for the nightly sweep,
whether its session finished the work or left a diagnosis, a next step or a
blocker. This lists those issues, leaving out the ones something will act on
without the sweep: an assignee, a queued or running Tend run, or an open pull
request that would close the issue. Whether a listed issue still holds work is
the sweep's judgement, so open pull requests that reference an issue without
closing it are reported as evidence rather than counted as its owner.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from tend.runtime.ci import active_subject_runs
from tend.runtime.shared import _common, bot_identity

# `last` rather than `first`: a long-lived issue, such as a CI failure report,
# collects hundreds of cross-references, and the open pull requests among them
# are the recent ones.
QUERY = """
query($owner: String!, $repo: String!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    issues(first: 100, after: $endCursor, states: OPEN) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        author { login }
        labels(first: 20) { nodes { name } }
        assignees(first: 1) { totalCount }
        comments(last: 1) { nodes { author { login } createdAt } }
        timelineItems(last: 100, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {
          nodes {
            __typename
            ... on CrossReferencedEvent {
              willCloseTarget
              source { ... on PullRequest { number state } }
            }
            ... on ConnectedEvent {
              subject { ... on PullRequest { number state } }
            }
          }
        }
      }
    }
  }
}
"""


def open_prs(issue: dict[str, Any]) -> tuple[list[int], bool]:
    """Return the open pull requests referencing *issue*, and whether one closes it."""
    numbers: list[int] = []
    closes = False
    for event in issue["timelineItems"]["nodes"]:
        pr = event.get("source") or event.get("subject") or {}
        if pr.get("state") != "OPEN":
            continue
        numbers.append(pr["number"])
        if event["__typename"] == "ConnectedEvent" or event.get("willCloseTarget"):
            closes = True
    return sorted(set(numbers)), closes


def select(
    issues: list[dict[str, Any]],
    *,
    bot: str,
    active: set[int],
) -> list[dict[str, Any]]:
    """Keep the issues the bot last wrote on that nothing else will act on."""
    selected = []
    for issue in issues:
        comment = next(iter(issue["comments"]["nodes"]), None)
        last = comment or {"author": issue["author"], "createdAt": None}
        if _common.actor_login(last["author"]) != bot:
            continue
        if issue["assignees"]["totalCount"] or issue["number"] in active:
            continue
        prs, closes = open_prs(issue)
        if closes:
            continue
        selected.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "url": issue["url"],
                "labels": [label["name"] for label in issue["labels"]["nodes"]],
                "last_comment_at": last["createdAt"],
                "open_prs": prs,
            }
        )
    return selected


def main() -> int:
    repo = _common.repository()
    owner, name = repo.split("/", 1)
    pages = _common.gh_pages(
        "graphql",
        "-f",
        f"query={QUERY}",
        "-f",
        f"owner={owner}",
        "-f",
        f"repo={name}",
    )
    issues = [
        issue
        for page in pages
        for issue in page["data"]["repository"]["issues"]["nodes"]
    ]
    runs = active_subject_runs.fetch_active_runs(repo)
    job_subjects = active_subject_runs.fetch_job_subjects(repo, runs)
    own_run_id = int(os.environ.get("GITHUB_RUN_ID") or 0)
    now = datetime.now(UTC)
    active = {
        issue["number"]
        for issue in issues
        if active_subject_runs.subject_runs(
            runs,
            subject_title=issue["title"],
            own_run_id=own_run_id,
            now=now,
            job_subjects=job_subjects,
            subject_number=issue["number"],
        )
    }
    bot, _ = bot_identity.read()
    _common.dump(
        {"open": len(issues), "issues": select(issues, bot=bot, active=active)}
    )
    return 0
