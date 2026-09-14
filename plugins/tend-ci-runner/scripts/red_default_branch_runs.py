# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Report the default branch's unfixed red runs.

The Actions run listings answer one URL with more than one snapshot: a read can
return a page built from an older index, coherent in itself but missing the
newest rows. Nothing downstream recovers a row a listing never returned, so the
red listing under-reports and the sweep can publish "the branch is green" while
a failure stands on it. The closure read fails the other way, serving a green
older than the true latest and leaving a fixed path reported as still red.

Both are handled the same way: re-read each URL until two consecutive answers
agree, then reduce across every answer seen -- union for the red rows, newest
for the green.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import github_cli

# One server-side filter per red conclusion, to reach past a busy repo's green
# runs. `cancelled` is left out: concurrency cancels dominate it.
RED_CONCLUSIONS = ("failure", "startup_failure", "timed_out")

# A listing that keeps moving is read this many times, then reported unconverged
# rather than read forever.
MAX_READS = 4

FIELDS = ("id", "name", "path", "event", "conclusion", "created_at")


def _rows(response: Any) -> list[dict[str, Any]]:
    runs = response.get("workflow_runs") or []
    return [
        {
            **{field: run.get(field) for field in FIELDS},
            "url": run.get("html_url"),
        }
        for run in runs
    ]


def converged_read(
    url: str, *, quiet: bool = False
) -> tuple[list[dict[str, Any]], bool]:
    """Read *url* until two consecutive answers agree; union what they returned.

    Returns the union keyed by run id and whether the reads agreed. A stale page
    is internally coherent and `total_count` moves with it, so agreement between
    consecutive reads is the only convergence signal the response carries.
    """
    seen: dict[int, dict[str, Any]] = {}
    previous: list[int] | None = None
    for _ in range(MAX_READS):
        rows = _rows(github_cli.json_call("api", url, quiet=quiet))
        for row in rows:
            seen.setdefault(int(row["id"]), row)
        ids = [int(row["id"]) for row in rows]
        if ids == previous:
            return list(seen.values()), True
        previous = ids
    return list(seen.values()), False


def latest_green(repo: str, branch: str, path: str) -> dict[str, Any] | None:
    """The newest green run of the workflow file at *path*, or None.

    `dynamic/dependabot/...` paths are generated rather than committed, so the
    per-workflow endpoint 404s on them; those rows have no closure and stay live.
    """
    basename = path.rsplit("/", 1)[-1]
    url = (
        f"repos/{repo}/actions/workflows/{basename}/runs"
        f"?branch={branch}&status=success&per_page=1"
    )
    try:
        rows, _ = converged_read(url, quiet=True)
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return None
        raise
    return max(rows, key=lambda row: row["created_at"]) if rows else None


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
    unconverged: list[str] = []
    for conclusion in RED_CONCLUSIONS:
        url = (
            f"repos/{repo}/actions/runs?branch={branch}&status={conclusion}&per_page=50"
        )
        rows, converged = converged_read(url)
        if not converged:
            unconverged.append(url)
        for row in rows:
            red[int(row["id"])] = row

    closures: dict[str, str | None] = {}
    live: list[dict[str, Any]] = []
    for row in sorted(red.values(), key=lambda row: row["created_at"], reverse=True):
        path = row["path"]
        if path not in closures:
            green = latest_green(repo, branch, path)
            closures[path] = green["created_at"] if green else None
        closed_at = closures[path]
        if closed_at and closed_at > row["created_at"]:
            continue
        live.append(row)

    github_cli.dump(
        {
            "branch": branch,
            "reached_back_to": min(
                (row["created_at"] for row in red.values()), default=None
            ),
            "paths_closed_by_later_green": {
                path: green for path, green in sorted(closures.items()) if green
            },
            "live": live,
            "unconverged_listings": unconverged,
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(github_cli.exit_code(error)) from None
