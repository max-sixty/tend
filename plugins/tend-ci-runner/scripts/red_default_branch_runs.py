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

A stale snapshot can also be durable: a cached entry answers every read of its
URL alike. So each read asks for a different `per_page`, which is a different
URL, and reads continue until two consecutive answers agree over the rows both
asked for. The answers are then reduced across every read -- union for the red
rows, newest for the green -- so one fresh read wins outright.
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


class Listing(NamedTuple):
    """One listing's answer: every row seen, the settled page, whether that
    page filled its `per_page`, and whether the reads settled.

    `rows` unions the reads so a row a later answer stopped returning is not
    lost. `page` is one answer alone -- the single page `per_page` bounded,
    and so the only one that says how far the endpoint was read.
    """

    rows: list[dict[str, Any]]
    page: list[dict[str, Any]]
    truncated: bool
    converged: bool


def page_sizes(first: int) -> list[int]:
    """The `per_page` of each read: *first*, then stepping down from it, or up
    where stepping down would reach an empty page."""
    step = -1 if first >= MAX_READS else 1
    return [first + step * read for read in range(MAX_READS)]


def paged(url: str, per_page: int) -> str:
    return f"{url}&per_page={per_page}"


def converged_read(url: str, per_page: int, *, quiet: bool = False) -> Listing:
    """Read *url* until two consecutive answers agree; union what they returned.

    A stale page is internally coherent and `total_count` moves with it, so
    agreement between reads is the only convergence signal the response
    carries -- and only between different URLs, since a cached entry agrees
    with itself. Each read therefore takes the next of `page_sizes(per_page)`,
    and two answers agree when they list the same rows up to the smaller size.
    The settled page is the larger of the two.
    """
    seen: dict[int, dict[str, Any]] = {}
    previous: tuple[list[dict[str, Any]], int] | None = None
    for size in page_sizes(per_page):
        page = _rows(github_cli.json_call("api", paged(url, size), quiet=quiet))
        for row in page:
            seen.setdefault(int(row["id"]), row)
        if previous is not None:
            common = min(size, previous[1])
            if _ids(page)[:common] == _ids(previous[0])[:common]:
                settled, settled_size = max(previous, (page, size), key=lambda p: p[1])
                truncated = len(settled) >= settled_size
                return Listing(list(seen.values()), settled, truncated, True)
        previous = (page, size)
    assert previous is not None
    page, size = previous
    return Listing(list(seen.values()), page, len(page) >= size, False)


def _ids(page: list[dict[str, Any]]) -> list[int]:
    return [int(row["id"]) for row in page]


def green_url(repo: str, branch: str, path: str) -> str:
    """The closure listing for the workflow file at *path*, without its
    `per_page`: only the newest row is needed, so the reads start at one."""
    basename = path.rsplit("/", 1)[-1]
    return (
        f"repos/{repo}/actions/workflows/{basename}/runs?branch={branch}&status=success"
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

    A 404 -- the workflow file is gone from the branch -- is a settled answer:
    there is no listing to converge. Generated runs never reach here; they close
    against `generated_greens` instead.
    """
    try:
        listing = converged_read(green_url(repo, branch, path), 1, quiet=True)
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return None, True
        raise
    rows = listing.rows
    green = max(rows, key=lambda row: row["created_at"]) if rows else None
    return green, listing.converged


def generated_green_url(repo: str, branch: str, workflow_id: int) -> str:
    """The closure listing for a generated workflow, addressed by its id,
    without its `per_page`."""
    return (
        f"repos/{repo}/actions/workflows/{workflow_id}/runs"
        f"?branch={branch}&status=success"
    )


def generated_greens(
    repo: str, branch: str, workflow_id: int
) -> tuple[dict[str, str], bool]:
    """The newest green run per `name` within one generated workflow.

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

    A 404 is a settled answer here as it is for a committed file that has left
    the branch: the id no longer resolves, so there is no listing to converge
    and the rows under it have no closure.
    """
    url = generated_green_url(repo, branch, workflow_id)
    try:
        listing = converged_read(url, GREEN_PAGE, quiet=True)
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return {}, True
        raise
    newest: dict[str, str] = {}
    for row in listing.rows:
        if row["created_at"] > newest.get(row["name"], ""):
            newest[row["name"]] = row["created_at"]
    return newest, listing.converged


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
        url = f"repos/{repo}/actions/runs?branch={branch}&status={conclusion}"
        listing = converged_read(url, PER_PAGE)
        if not listing.converged:
            unconverged.append(paged(url, PER_PAGE))
        # Truncation and the floor come from the settled page, not the union: a
        # stale read answers from its own window, so a union across the two
        # reaches back past everything the settled listing read.
        if listing.truncated:
            floors.append(min(row["created_at"] for row in listing.page))
        for row in listing.rows:
            red[int(row["id"])] = row

    # One generated path answers under a name per subject, so its closure is
    # keyed by both. A committed file answers under its path alone: `run-name:`
    # and a rename both move a run's `name` without changing which listing
    # closes it, so keying those by name too would re-read one URL per name.
    closures: dict[tuple[str, str], str | None] = {}
    generated: dict[int, dict[str, str]] = {}
    live: list[dict[str, Any]] = []
    for row in sorted(red.values(), key=lambda row: row["created_at"], reverse=True):
        is_generated = row["path"].startswith(GENERATED_PREFIX)
        subject = (row["path"], row["name"] if is_generated else "")
        if subject not in closures:
            if is_generated:
                workflow_id = row["workflow_id"]
                if workflow_id not in generated:
                    greens, converged = generated_greens(repo, branch, workflow_id)
                    generated[workflow_id] = greens
                    if not converged:
                        unconverged.append(
                            paged(
                                generated_green_url(repo, branch, workflow_id),
                                GREEN_PAGE,
                            )
                        )
                closures[subject] = generated[workflow_id].get(row["name"])
            else:
                green, converged = latest_green(repo, branch, row["path"])
                if not converged:
                    unconverged.append(paged(green_url(repo, branch, row["path"]), 1))
                closures[subject] = green["created_at"] if green else None
        closed_at = closures[subject]
        if closed_at and closed_at > row["created_at"]:
            continue
        live.append(row)

    # Published per path: the newest green read for any of its red subjects,
    # whether or not it closed one.
    green_by_path: dict[str, str] = {}
    for (path, _), green in closures.items():
        if green and green > green_by_path.get(path, ""):
            green_by_path[path] = green

    github_cli.dump(
        {
            "branch": branch,
            # The newest floor among the truncated listings. An untruncated
            # listing returned its whole history, so it constrains nothing, and
            # null means none was truncated.
            "reached_back_to": max(floors, default=None),
            # The closure evidence, not a closed set: a green older than a
            # path's red rows closes none of them, and those rows are in `live`.
            "latest_green_by_path": dict(sorted(green_by_path.items())),
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
