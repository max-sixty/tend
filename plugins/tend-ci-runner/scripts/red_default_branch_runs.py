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
from typing import Any, NamedTuple

import github_cli

# One server-side filter per red conclusion, to reach past a busy repo's green
# runs. `cancelled` is left out: concurrency cancels dominate it.
RED_CONCLUSIONS = ("failure", "startup_failure", "timed_out")

# A listing that keeps moving is read this many times, then reported unconverged
# rather than read forever.
MAX_READS = 4

# One page per listing, unpaginated. A listing that fills its page is truncated,
# and its oldest row is as far back as the sweep saw that conclusion.
PER_PAGE = 50

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


class Listing(NamedTuple):
    """One URL's answer: every row seen, the last page read, whether it settled.

    `rows` unions the reads so a row a later answer stopped returning is not
    lost. `page` is the last answer alone -- the single page `per_page` bounded,
    and so the only one that says how far the endpoint was read.
    """

    rows: list[dict[str, Any]]
    page: list[dict[str, Any]]
    converged: bool


def converged_read(url: str, *, quiet: bool = False) -> Listing:
    """Read *url* until two consecutive answers agree; union what they returned.

    A stale page is internally coherent and `total_count` moves with it, so
    agreement between consecutive reads is the only convergence signal the
    response carries.
    """
    seen: dict[int, dict[str, Any]] = {}
    page: list[dict[str, Any]] = []
    previous: list[int] | None = None
    for _ in range(MAX_READS):
        page = _rows(github_cli.json_call("api", url, quiet=quiet))
        for row in page:
            seen.setdefault(int(row["id"]), row)
        ids = [int(row["id"]) for row in page]
        if ids == previous:
            return Listing(list(seen.values()), page, True)
        previous = ids
    return Listing(list(seen.values()), page, False)


def green_url(repo: str, branch: str, path: str) -> str:
    """The closure listing for the workflow file at *path*."""
    basename = path.rsplit("/", 1)[-1]
    return (
        f"repos/{repo}/actions/workflows/{basename}/runs"
        f"?branch={branch}&status=success&per_page=1"
    )


def latest_green(
    repo: str, branch: str, path: str
) -> tuple[dict[str, Any] | None, bool]:
    """The newest green run of the workflow file at *path*, and whether the
    listing settled.

    An unsettled closure listing can serve a green older than the true latest,
    which reports a fixed path as still red -- the mirror of the red listing's
    failure -- so the caller names the URL rather than publishing the sweep as
    complete.

    `dynamic/dependabot/...` paths are generated rather than committed, so the
    per-workflow endpoint 404s on them; those rows have no closure and stay
    live. A 404 is a settled answer: there is no listing to converge.
    """
    try:
        listing = converged_read(green_url(repo, branch, path), quiet=True)
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return None, True
        raise
    rows = listing.rows
    green = max(rows, key=lambda row: row["created_at"]) if rows else None
    return green, listing.converged


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
    floors: list[str] = []
    for conclusion in RED_CONCLUSIONS:
        url = (
            f"repos/{repo}/actions/runs"
            f"?branch={branch}&status={conclusion}&per_page={PER_PAGE}"
        )
        listing = converged_read(url)
        if not listing.converged:
            unconverged.append(url)
        # Truncation and the floor come from the settled page, not the union: a
        # stale read answers from its own window, so a union across the two
        # reaches back past everything the settled listing read.
        if len(listing.page) >= PER_PAGE:
            floors.append(min(row["created_at"] for row in listing.page))
        for row in listing.rows:
            red[int(row["id"])] = row

    closures: dict[str, str | None] = {}
    live: list[dict[str, Any]] = []
    for row in sorted(red.values(), key=lambda row: row["created_at"], reverse=True):
        path = row["path"]
        if path not in closures:
            green, converged = latest_green(repo, branch, path)
            if not converged:
                unconverged.append(green_url(repo, branch, path))
            closures[path] = green["created_at"] if green else None
        closed_at = closures[path]
        if closed_at and closed_at > row["created_at"]:
            continue
        live.append(row)

    github_cli.dump(
        {
            "branch": branch,
            # The newest floor among the truncated listings. An untruncated
            # listing returned its whole history, so it constrains nothing, and
            # null means none was truncated.
            "reached_back_to": max(floors, default=None),
            # The closure evidence, not a closed set: a green older than a
            # path's red rows closes none of them, and those rows are in `live`.
            "latest_green_by_path": {
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
