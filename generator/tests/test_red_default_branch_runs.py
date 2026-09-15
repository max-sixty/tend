"""Tests for red_default_branch_runs.py — the live-work red-run sweep.

The Actions listings answer one URL from more than one snapshot, and reading
the stale one costs an outward action: the omitted rows are the newest, so the
sweep publishes "the default branch is green" while a failure stands on it.
Convergence is the behaviour under test — the fake `gh` serves a different page
per read of the same URL, which is what the real endpoint does.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests import GH_PREAMBLE, fake_bin, tool_path, uv_script

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "tend-ci-runner"
    / "scripts"
    / "red_default_branch_runs.py"
)
CI = ".github/workflows/ci.yaml"
DEPENDABOT = "dynamic/dependabot/dependabot-updates"
CODE_SCANNING = "dynamic/github-code-scanning/codeql"
# The generated paths name no committed file, so their closure listing is
# addressed by the workflow id every run row carries.
CODE_SCANNING_ID = 348682278
DEPENDABOT_ID = 348683058
# red_default_branch_runs.PER_PAGE: a listing this long is truncated, and a
# shorter one returned everything there is.
PER_PAGE = 50

# Reads of the same URL are answered from `$RUNS_DIR/<prefix>-<n>.json`, one
# file per read, falling back to the newest staged file once the reads outrun
# them — so a single staged page is a consistent endpoint and several are a
# moving one. Both listings the script reads work this way: the red rows under
# the conclusion's name, the closure read under `green-<workflow>`.
FAKE_GH = (
    GH_PREAMBLE
    + r"""
serve() {
  counter="$RUNS_DIR/count-$1"
  n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
  printf '%s' "$n" > "$counter"
  i="$n"
  while [ "$i" -ge 1 ]; do
    if [ -f "$RUNS_DIR/$1-$i.json" ]; then emit "$(cat "$RUNS_DIR/$1-$i.json")"; return 0; fi
    i=$(( i - 1 ))
  done
  return 1
}

case "$*" in
  "repo view"*) emit '{"defaultBranchRef": {"name": "main"}}' ;;
  *"/actions/runs?"*)
    args="$*"
    status="${args#*status=}"
    status="${status%%&*}"
    serve "$status" || emit '{"workflow_runs": []}'
    ;;
  *"/actions/workflows/"*)
    args="$*"
    workflow="${args#*/actions/workflows/}"
    workflow="${workflow%%/runs*}"
    serve "green-$workflow" || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
    ;;
  *) exit 1 ;;
esac
"""
)


def _red(
    rid: int,
    created_at: str,
    *,
    path: str = CI,
    name: str = "ci",
    workflow_id: int = 250586625,
) -> dict:
    return {
        "id": rid,
        "name": name,
        "path": path,
        "event": "push",
        "conclusion": "failure",
        "created_at": created_at,
        "workflow_id": workflow_id,
        "html_url": f"https://github.com/owner/repo/actions/runs/{rid}",
    }


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """Fake `gh` over a repository whose every listing is empty."""
    bindir = fake_bin(tmp_path, gh=FAKE_GH)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return {
        "PATH": tool_path(bindir),
        "GH_CALLS": str(tmp_path / "gh-calls.log"),
        "GITHUB_REPOSITORY": "owner/repo",
        "RUNS_DIR": str(runs_dir),
    }


def _page(env: dict[str, str], status: str, read: int, *runs: dict) -> None:
    """Stage the answer the *read*-th call to `status=<status>` receives."""
    path = Path(env["RUNS_DIR"]) / f"{status}-{read}.json"
    path.write_text(json.dumps({"workflow_runs": list(runs), "total_count": len(runs)}))


def _green(
    env: dict[str, str],
    workflow: str,
    created_at: str,
    *,
    read: int = 1,
    rid: int = 1,
) -> None:
    """Stage the answer the *read*-th closure read of *workflow* receives."""
    path = Path(env["RUNS_DIR"]) / f"green-{workflow}-{read}.json"
    path.write_text(
        json.dumps(
            {
                "workflow_runs": [
                    {
                        "id": rid,
                        "name": "ci",
                        "path": f".github/workflows/{workflow}",
                        "event": "push",
                        "conclusion": "success",
                        "created_at": created_at,
                    }
                ]
            }
        )
    )


def _generated_green(
    env: dict[str, str],
    workflow_id: int,
    *runs: tuple[int, str, str],
    read: int = 1,
) -> None:
    """Stage the *read*-th closure read of the generated workflow *workflow_id*.

    Each run is `(id, name, created_at)` -- the whole listing is one workflow's,
    so `name` is what separates the subjects within it.
    """
    path = Path(env["RUNS_DIR"]) / f"green-{workflow_id}-{read}.json"
    path.write_text(
        json.dumps(
            {
                "workflow_runs": [
                    {
                        "id": rid,
                        "name": name,
                        "path": CODE_SCANNING
                        if workflow_id == CODE_SCANNING_ID
                        else DEPENDABOT,
                        "event": "dynamic",
                        "conclusion": "success",
                        "created_at": created_at,
                        "workflow_id": workflow_id,
                    }
                    for rid, name, created_at in runs
                ]
            }
        )
    )


def _sweep(env: dict[str, str]) -> dict:
    result = subprocess.run(
        uv_script(SCRIPT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_stale_first_page_does_not_hide_the_newest_failure(
    env: dict[str, str],
) -> None:
    """The regression: the rows a stale page omits are the newest ones.

    Nothing downstream recovers a row the listing never returned, so a sweep
    that read once would report the branch green with run 200 red on it.
    """
    _page(env, "failure", 1, _red(100, "2026-09-01T00:00:00Z"))
    _page(
        env,
        "failure",
        2,
        _red(200, "2026-09-14T00:00:00Z"),
        _red(100, "2026-09-01T00:00:00Z"),
    )
    _green(env, "ci.yaml", "2026-08-01T00:00:00Z")

    sweep = _sweep(env)

    assert [row["id"] for row in sweep["live"]] == [200, 100]
    assert sweep["unconverged_listings"] == []
    # Neither listing filled its page, so the sweep saw every red run there is.
    assert sweep["reached_back_to"] is None


def test_a_consistent_listing_is_read_twice(env: dict[str, str]) -> None:
    """Agreement between consecutive reads is the only convergence signal the
    response carries, so the cheapest possible answer is still two reads."""
    _page(env, "failure", 1, _red(100, "2026-09-01T00:00:00Z"))
    _green(env, "ci.yaml", "2026-08-01T00:00:00Z")

    _sweep(env)

    calls = Path(env["GH_CALLS"]).read_text().splitlines()
    for status in ("failure", "startup_failure", "timed_out"):
        assert sum(f"status={status}&" in call for call in calls) == 2


def test_an_empty_listing_is_re_read_before_it_is_believed(
    env: dict[str, str],
) -> None:
    """An empty answer is the one a sweep most needs to re-check: it is what a
    snapshot older than every row returns, and it reads as an all-clear."""
    _page(env, "failure", 2, _red(200, "2026-09-14T00:00:00Z"))
    _green(env, "ci.yaml", "2026-08-01T00:00:00Z")

    assert [row["id"] for row in _sweep(env)["live"]] == [200]


def test_a_listing_that_never_settles_is_reported_as_such(
    env: dict[str, str],
) -> None:
    """Capped reads, then the URL is named — a sweep that publishes an
    unsettled listing as complete is the failure this exists to prevent."""
    for read in (1, 2, 3, 4):
        _page(env, "failure", read, _red(100 + read, f"2026-09-0{read}T00:00:00Z"))
    _green(env, "ci.yaml", "2026-08-01T00:00:00Z")

    sweep = _sweep(env)

    assert sweep["unconverged_listings"] == [
        "repos/owner/repo/actions/runs?branch=main&status=failure&per_page=50"
    ]
    # Everything seen across the capped reads is still reported.
    assert [row["id"] for row in sweep["live"]] == [104, 103, 102, 101]


def test_a_closure_listing_that_never_settles_is_reported_as_such(
    env: dict[str, str],
) -> None:
    """The closure read fails the other way round — an older green reported as
    the latest leaves a fixed path red — so an unsettled one is named too."""
    _page(env, "failure", 1, _red(100, "2026-09-10T00:00:00Z"))
    for read in (1, 2, 3, 4):
        _green(env, "ci.yaml", f"2026-09-0{read}T00:00:00Z", read=read, rid=read)

    sweep = _sweep(env)

    assert sweep["unconverged_listings"] == [
        (
            "repos/owner/repo/actions/workflows/ci.yaml/runs"
            "?branch=main&status=success&per_page=1"
        )
    ]
    # The newest green seen across the capped reads still closes what it can:
    # here every one of them predates the red row, so it stays live -- the map
    # is the closure evidence read, not a set of paths something closed.
    assert [row["id"] for row in sweep["live"]] == [100]
    assert sweep["latest_green_by_path"] == {CI: "2026-09-04T00:00:00Z"}


def test_coverage_stops_at_the_listing_that_ran_out_of_page(
    env: dict[str, str],
) -> None:
    """`reached_back_to` is the scope the published claim rests on, so it has to
    name the window every conclusion was read over — the newest floor among the
    truncated listings, not the older row a short listing happens to reach."""
    _page(
        env,
        "failure",
        1,
        *(
            _red(400 + n, f"2026-09-01T00:{n:02d}:00Z")
            for n in reversed(range(PER_PAGE))
        ),
    )
    # A second truncated listing, reaching further back: `reached_back_to` is
    # the newest floor among them, which is what separates `max` from `min`.
    _page(
        env,
        "timed_out",
        1,
        *(
            _red(500 + n, f"2026-07-01T00:{n:02d}:00Z")
            for n in reversed(range(PER_PAGE))
        ),
    )
    _page(env, "startup_failure", 1, _red(300, "2026-06-01T00:00:00Z"))
    _green(env, "ci.yaml", "2026-05-01T00:00:00Z")

    sweep = _sweep(env)

    assert sweep["reached_back_to"] == "2026-09-01T00:00:00Z"
    # The June row is still reported; it is the *coverage* claim that stops at
    # September, not the listing of what was found.
    assert 300 in [row["id"] for row in sweep["live"]]


def test_the_coverage_floor_comes_from_the_settled_page(
    env: dict[str, str],
) -> None:
    """A stale read answers from its own window, so a floor taken across the
    union claims coverage of the gap between that window and the settled one --
    the over-claim `reached_back_to` exists to bound."""
    # Read 1 is the stale snapshot: a full page from a July window.
    _page(
        env,
        "failure",
        1,
        *(
            _red(500 + n, f"2026-07-01T00:{n:02d}:00Z")
            for n in reversed(range(PER_PAGE))
        ),
    )
    # Reads 2 and 3 settle on a full page from a September window, so nothing
    # between July and September was ever read.
    _page(
        env,
        "failure",
        2,
        *(
            _red(400 + n, f"2026-09-01T00:{n:02d}:00Z")
            for n in reversed(range(PER_PAGE))
        ),
    )
    _green(env, "ci.yaml", "2026-05-01T00:00:00Z")

    sweep = _sweep(env)

    assert sweep["reached_back_to"] == "2026-09-01T00:00:00Z"
    # The stale page's rows are still reported: the union is what keeps a row a
    # later read stopped returning, and only the coverage claim is bounded.
    assert len(sweep["live"]) == 2 * PER_PAGE


def test_a_later_green_closes_the_path(env: dict[str, str]) -> None:
    """The closure read is what keeps a weeks-deep listing from re-reporting
    failures somebody already fixed."""
    _page(env, "failure", 1, _red(100, "2026-09-01T00:00:00Z"))
    _green(env, "ci.yaml", "2026-09-02T00:00:00Z")

    sweep = _sweep(env)

    assert sweep["live"] == []
    assert sweep["latest_green_by_path"] == {CI: "2026-09-02T00:00:00Z"}


def test_a_committed_workflow_whose_file_is_gone_stays_live(
    env: dict[str, str],
) -> None:
    """A deleted workflow file 404s the closure endpoint, which is a settled
    answer rather than an error: the row has no closure and stays live, and the
    sweep still reports the rest of the branch."""
    _page(env, "failure", 1, _red(250, "2026-09-01T00:00:00Z"))

    sweep = _sweep(env)

    assert [row["id"] for row in sweep["live"]] == [250]
    assert sweep["latest_green_by_path"] == {}
    assert sweep["unconverged_listings"] == []


def test_a_generated_update_that_never_repeats_its_name_stays_live(
    env: dict[str, str],
) -> None:
    """Dependabot's updates share one workflow, so its closure listing is full
    of greens -- but each update's name carries a one-off id, so none of them
    is the same subject as the failed one."""
    _page(
        env,
        "failure",
        1,
        _red(
            300,
            "2026-09-01T00:00:00Z",
            path=DEPENDABOT,
            name="uv in /. for tornado - Update #1",
            workflow_id=DEPENDABOT_ID,
        ),
    )
    _generated_green(
        env,
        DEPENDABOT_ID,
        (301, "uv in /. for h2 - Update #2", "2026-09-02T00:00:00Z"),
    )

    sweep = _sweep(env)

    assert [row["id"] for row in sweep["live"]] == [300]
    assert sweep["latest_green_by_path"] == {}


def test_a_later_green_closes_a_generated_run_of_the_same_name(
    env: dict[str, str],
) -> None:
    """Code scanning carries the same name on every push, so a passing analysis
    closes a failed one — but its path names no committed file, so only the
    listing addressed by workflow id can say so."""
    _page(
        env,
        "failure",
        1,
        _red(
            400,
            "2026-09-13T17:20:53Z",
            path=CODE_SCANNING,
            name="Push on main",
            workflow_id=CODE_SCANNING_ID,
        ),
    )
    _generated_green(
        env, CODE_SCANNING_ID, (401, "Push on main", "2026-09-13T17:24:00Z")
    )

    sweep = _sweep(env)

    assert sweep["live"] == []
    assert sweep["latest_green_by_path"] == {CODE_SCANNING: "2026-09-13T17:24:00Z"}


def test_a_generated_green_older_than_the_failure_closes_nothing(
    env: dict[str, str],
) -> None:
    """The closure is `newer than the red row`, not `exists`: the same analysis
    passing before it failed leaves the failure standing."""
    _page(
        env,
        "failure",
        1,
        _red(
            410,
            "2026-09-13T17:20:53Z",
            path=CODE_SCANNING,
            name="Push on main",
            workflow_id=CODE_SCANNING_ID,
        ),
    )
    _generated_green(
        env, CODE_SCANNING_ID, (411, "Push on main", "2026-09-12T00:00:00Z")
    )

    sweep = _sweep(env)

    assert [row["id"] for row in sweep["live"]] == [410]


def test_an_unsettled_generated_green_listing_is_reported_as_such(
    env: dict[str, str],
) -> None:
    """A moving green listing can serve a page without the passing analysis in
    it, which reports a fixed path as still red — the same over-claim the red
    listing's convergence loop exists to prevent, reached from the other side."""
    _page(
        env,
        "failure",
        1,
        _red(
            420,
            "2026-09-13T17:20:53Z",
            path=CODE_SCANNING,
            name="Push on main",
            workflow_id=CODE_SCANNING_ID,
        ),
    )
    for read in range(1, 5):
        _generated_green(
            env,
            CODE_SCANNING_ID,
            (430 + read, "Push on main", f"2026-09-1{read}T00:00:00Z"),
            read=read,
        )

    sweep = _sweep(env)

    assert sweep["unconverged_listings"] == [
        (
            f"repos/owner/repo/actions/workflows/{CODE_SCANNING_ID}/runs"
            "?branch=main&status=success&per_page=100"
        )
    ]


def test_a_committed_path_is_read_once_whatever_its_runs_are_named(
    env: dict[str, str],
) -> None:
    """A committed workflow answers under its path: `run-name:` and a rename
    both move a run's `name`, and the closure listing is the same either way.
    Keying those rows by name would re-read one URL per name for no new answer.
    """
    _page(
        env,
        "failure",
        1,
        _red(600, "2026-09-01T00:00:00Z", name="continuous integration"),
        _red(601, "2026-08-01T00:00:00Z", name="ci"),
    )
    _green(env, "ci.yaml", "2026-09-02T00:00:00Z")

    sweep = _sweep(env)

    assert sweep["live"] == []
    assert sweep["latest_green_by_path"] == {CI: "2026-09-02T00:00:00Z"}
    reads = [
        line
        for line in Path(env["GH_CALLS"]).read_text().splitlines()
        if "/actions/workflows/ci.yaml/runs" in line
    ]
    # One `converged_read`: two answers that agree, and no third.
    assert len(reads) == 2


def test_a_generated_workflow_with_no_listing_stays_live(
    env: dict[str, str],
) -> None:
    """A 404 on the id-addressed listing is a settled answer, as it is for a
    committed file that has left the branch: the rows under it have no closure.
    Raising instead would lose the whole sweep over one unresolvable id."""
    _page(
        env,
        "failure",
        1,
        _red(
            700,
            "2026-09-13T17:20:53Z",
            path=CODE_SCANNING,
            name="Push on main",
            workflow_id=CODE_SCANNING_ID,
        ),
    )

    sweep = _sweep(env)

    assert [row["id"] for row in sweep["live"]] == [700]
    assert sweep["latest_green_by_path"] == {}
    assert sweep["unconverged_listings"] == []


def test_one_subject_of_a_generated_path_closes_without_closing_the_others(
    env: dict[str, str],
) -> None:
    """`latest_green_by_path` reduces a per-subject closure onto the path, so a
    generated path's published green can be the one that closed a different
    name. The row it did not close stays live even though it is older."""
    _page(
        env,
        "failure",
        1,
        _red(
            500,
            "2026-09-13T00:00:00Z",
            path=CODE_SCANNING,
            name="Push on main",
            workflow_id=CODE_SCANNING_ID,
        ),
        _red(
            501,
            "2026-09-14T00:00:00Z",
            path=CODE_SCANNING,
            name="Scheduled",
            workflow_id=CODE_SCANNING_ID,
        ),
    )
    _generated_green(
        env, CODE_SCANNING_ID, (502, "Push on main", "2026-09-15T00:00:00Z")
    )

    sweep = _sweep(env)

    assert [row["id"] for row in sweep["live"]] == [501]
    # Older than the green published for its own path: the green closed
    # `Push on main`, and nothing has closed `Scheduled`.
    assert sweep["latest_green_by_path"] == {CODE_SCANNING: "2026-09-15T00:00:00Z"}
