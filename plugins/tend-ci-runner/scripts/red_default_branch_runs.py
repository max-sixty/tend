# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Find candidate unfixed default-branch failures in GitHub's returned pages.

Each listing is read once. Its rows are observations, not a freshness guarantee:
repeated agreement cannot distinguish a current page from a durably stale one.
A later observed green closes a failure of the same subject; remaining failures
are candidates for investigation before any outward action.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import github_cli

# One server-side filter per red conclusion, to reach past a busy repo's green
# runs. `cancelled` is left out: concurrency cancels dominate it.
RED_CONCLUSIONS = ("failure", "startup_failure", "timed_out")

# One page per listing, unpaginated. A listing that fills its page is truncated,
# and its oldest row is as far back as the sweep saw that conclusion.
PER_PAGE = 50

# Runs GitHub generates rather than running from a committed file.
GENERATED_PREFIX = "dynamic/"

# The generated runs' closure listing is read as one page: a subject that has
# not passed within its own workflow's last this-many greens keeps the closure
# it had, none.
GREEN_PAGE = 100

FIELDS = ("id", "name", "path", "event", "conclusion", "created_at", "workflow_id")


def _rows(response: Any) -> list[dict[str, Any]]:
    runs = response.get("workflow_runs") or []
    return [
        {
            **{field: run.get(field) for field in FIELDS},
            "url": run.get("html_url"),
        }
        for run in runs
    ]


def read_listing(url: str, *, quiet: bool = False) -> list[dict[str, Any]]:
    """Read one API page without claiming that its snapshot is current."""
    return _rows(github_cli.json_call("api", url, quiet=quiet))


def green_url(repo: str, branch: str, path: str) -> str:
    """The closure listing for a committed workflow file."""
    basename = path.rsplit("/", 1)[-1]
    return f"repos/{repo}/actions/workflows/{basename}/runs?branch={branch}&status=success&per_page=1"


def observed_green(repo: str, branch: str, path: str) -> dict[str, Any] | None:
    """The newest green returned for a committed file, or none on a 404."""
    try:
        rows = read_listing(green_url(repo, branch, path), quiet=True)
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return None
        raise
    return max(rows, key=lambda row: row["created_at"]) if rows else None


def generated_green_url(repo: str, branch: str, workflow_id: int) -> str:
    """The closure listing for a generated workflow, addressed by its id."""
    return (
        f"repos/{repo}/actions/workflows/{workflow_id}/runs"
        f"?branch={branch}&status=success&per_page={GREEN_PAGE}"
    )


def generated_greens(repo: str, branch: str, workflow_id: int) -> dict[str, str]:
    """The newest observed green per `name` within one generated workflow.

    `green_url` addresses the per-workflow endpoint by the path's basename,
    which 404s for a `dynamic/...` path because it names no committed file. The
    same endpoint serves these runs when addressed by `workflow_id`, so the
    page is spent on this workflow's own greens rather than on whatever else
    ran on the branch.

    The name is what separates the subjects sharing that id: a code-scanning
    analysis carries the same name on every push, so a passing one closes a
    failed one, while each Dependabot update's name carries a one-off id that
    never recurs -- which is why those rows close through a fix PR or a tracker
    and not here.

    A 404 means the id no longer resolves; no closure evidence was returned.
    """
    url = generated_green_url(repo, branch, workflow_id)
    try:
        rows = read_listing(url, quiet=True)
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return {}
        raise
    newest: dict[str, str] = {}
    for row in rows:
        if row["created_at"] > newest.get(row["name"], ""):
            newest[row["name"]] = row["created_at"]
    return newest


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(f"usage: {sys.argv[0]}", file=sys.stderr)
        return 2

    repo = github_cli.repository()
    branch = github_cli.json_call("repo", "view", repo, "--json", "defaultBranchRef")[
        "defaultBranchRef"
    ]["name"]

    red: dict[int, dict[str, Any]] = {}
    floors: list[str] = []
    for conclusion in RED_CONCLUSIONS:
        url = (
            f"repos/{repo}/actions/runs"
            f"?branch={branch}&status={conclusion}&per_page={PER_PAGE}"
        )
        rows = read_listing(url)
        if len(rows) >= PER_PAGE:
            floors.append(min(row["created_at"] for row in rows))
        for row in rows:
            red[int(row["id"])] = row

    # One generated path answers under a name per subject, so its closure is
    # keyed by both. A committed file answers under its path alone: `run-name:`
    # and a rename both move a run's `name` without changing which listing
    # closes it, so keying those by name too would re-read one URL per name.
    closures: dict[tuple[str, str], str | None] = {}
    generated: dict[int, dict[str, str]] = {}
    candidates: list[dict[str, Any]] = []
    for row in sorted(red.values(), key=lambda row: row["created_at"], reverse=True):
        is_generated = row["path"].startswith(GENERATED_PREFIX)
        subject = (row["path"], row["name"] if is_generated else "")
        if subject not in closures:
            if is_generated:
                workflow_id = row["workflow_id"]
                if workflow_id not in generated:
                    generated[workflow_id] = generated_greens(repo, branch, workflow_id)
                closures[subject] = generated[workflow_id].get(row["name"])
            else:
                green = observed_green(repo, branch, row["path"])
                closures[subject] = green["created_at"] if green else None
        closed_at = closures[subject]
        if closed_at and closed_at > row["created_at"]:
            continue
        candidates.append(row)

    # Published per path: the newest green read for any of its red subjects,
    # whether or not it closed one.
    green_by_path: dict[str, str] = {}
    for (path, _), green in closures.items():
        if green and green > green_by_path.get(path, ""):
            green_by_path[path] = green

    github_cli.dump(
        {
            "branch": branch,
            # The newest floor among the truncated listings. Null means none of the returned
            # pages filled its limit; it says nothing about freshness.
            "reached_back_to": max(floors, default=None),
            # The closure evidence, not a closed set: a green older than a
            # path's red rows closes none of them, and those rows are candidates.
            "observed_green_by_path": dict(sorted(green_by_path.items())),
            "candidates": candidates,
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
