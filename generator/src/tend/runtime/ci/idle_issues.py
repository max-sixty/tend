"""List the open issues nothing else will act on, for the sweeps that restart them.

A comment on a thread the bot is in starts an event workflow, and the
notifications poll recovers what those runs miss, but the bot gets no event for
its own writes. So work a bot session left on an issue waits for a sweep.
Something else will act on an issue with an assignee other than the bot, a
queued or running Tend run, or an open pull request that will close it, so
neither list includes those.

- ``causes``: open CI cause issues, for nightly to restart. A cause waiting on a
  person is assigned to them, so one that is left has stalled, whoever wrote
  last.
- ``others``: the remaining open issues the bot had the last word on, for
  weekly to read. Nothing on GitHub tells a stalled issue from one waiting to be
  prioritised, so the URLs of the open pull requests, in any repository, that
  reference each one come back as evidence for that judgement. CI failure reports are nightly's, and Tend's
  bookkeeping trackers hold no work, so neither is listed.

Comments by GitHub Apps (``github-actions``, stale bots) start no Tend workflow,
so they don't take the last word.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from tend.runtime.ci import active_subject_runs, review_reviewers, review_runs
from tend.runtime.shared import _common, bot_identity, ci_failure_report
from tend.workflows import BOOKKEEPING_LABELS

CAUSE_LABEL = "tend-ci-cause"
NOT_OTHERS = frozenset(
    {ci_failure_report.LABEL, review_runs.LABEL, review_reviewers.LABEL}
    | set(BOOKKEEPING_LABELS)
)

# Cross-references are read from the end: a long-lived issue, such as a CI
# failure report, collects hundreds, and the open pull requests are the recent
# ones.
QUERY = """
query($owner: String!, $repo: String!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    issues(first: 100, after: $endCursor, states: OPEN) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        author { __typename login }
        labels(first: 20) { nodes { name } }
        assignees(first: 10) { nodes { login } }
        comments(last: 10) { nodes { author { __typename login } createdAt } }
        closedByPullRequestsReferences(first: 1) { totalCount }
        timelineItems(last: 100, itemTypes: [CROSS_REFERENCED_EVENT]) {
          nodes {
            ... on CrossReferencedEvent {
              source { ... on PullRequest { url state } }
            }
          }
        }
      }
    }
  }
}
"""


def last_word(issue: dict[str, Any]) -> tuple[str, str | None]:
    """Return who last wrote on *issue*, apps aside, and when, if in a comment."""
    for comment in reversed(issue["comments"]["nodes"]):
        author = comment["author"] or {}
        if author.get("__typename") != "Bot":
            return _common.actor_login(author), comment["createdAt"]
    return _common.actor_login(issue["author"]), None


def select(
    issues: list[dict[str, Any]],
    *,
    bot: str,
    active: set[int],
) -> dict[str, list[dict[str, Any]]]:
    """Split the issues nothing else will act on into ``causes`` and ``others``."""
    causes = []
    others = []
    for issue in issues:
        if issue["number"] in active:
            continue
        if any(a["login"] != bot for a in issue["assignees"]["nodes"]):
            continue
        if issue["closedByPullRequestsReferences"]["totalCount"]:
            continue
        listed = {
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["url"],
        }
        labels = [label["name"] for label in issue["labels"]["nodes"]]
        if CAUSE_LABEL in labels:
            causes.append(listed)
            continue
        author, at = last_word(issue)
        if author != bot or NOT_OTHERS.intersection(labels):
            continue
        prs = {
            event["source"]["url"]
            for event in issue["timelineItems"]["nodes"]
            if (event.get("source") or {}).get("state") == "OPEN"
        }
        others.append(
            {
                **listed,
                "labels": labels,
                "last_comment_at": at,
                "open_prs": sorted(prs),
            }
        )
    return {"causes": causes, "others": others}


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
    _common.dump({"open": len(issues), **select(issues, bot=bot, active=active)})
    return 0
