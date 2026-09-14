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

# Reads of the same URL are answered from `$RUNS_DIR/<status>-<n>.json`, one
# file per read, falling back to the newest staged file once the reads outrun
# them — so a single staged page is a consistent endpoint and several are a
# moving one.
FAKE_GH = (
    GH_PREAMBLE
    + r"""
case "$*" in
  "repo view"*) emit '{"defaultBranchRef": {"name": "main"}}' ;;
  *"/actions/runs?"*)
    args="$*"
    status="${args#*status=}"
    status="${status%%&*}"
    counter="$RUNS_DIR/count-$status"
    n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
    printf '%s' "$n" > "$counter"
    file=""
    i="$n"
    while [ "$i" -ge 1 ]; do
      if [ -f "$RUNS_DIR/$status-$i.json" ]; then file="$RUNS_DIR/$status-$i.json"; break; fi
      i=$(( i - 1 ))
    done
    if [ -n "$file" ]; then emit "$(cat "$file")"; else emit '{"workflow_runs": []}'; fi
    ;;
  *"/actions/workflows/"*)
    args="$*"
    workflow="${args#*/actions/workflows/}"
    workflow="${workflow%%/runs*}"
    file="$RUNS_DIR/green-$workflow.json"
    if [ -f "$file" ]; then
      emit "$(cat "$file")"
    else
      echo "gh: Not Found (HTTP 404)" >&2
      exit 1
    fi
    ;;
  *) exit 1 ;;
esac
"""
)


def _red(rid: int, created_at: str, *, path: str = CI) -> dict:
    return {
        "id": rid,
        "name": "ci",
        "path": path,
        "event": "push",
        "conclusion": "failure",
        "created_at": created_at,
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


def _green(env: dict[str, str], workflow: str, created_at: str) -> None:
    path = Path(env["RUNS_DIR"]) / f"green-{workflow}.json"
    path.write_text(
        json.dumps(
            {
                "workflow_runs": [
                    {
                        "id": 1,
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
    assert sweep["reached_back_to"] == "2026-09-01T00:00:00Z"


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


def test_a_later_green_closes_the_path(env: dict[str, str]) -> None:
    """The closure read is what keeps a weeks-deep listing from re-reporting
    failures somebody already fixed."""
    _page(env, "failure", 1, _red(100, "2026-09-01T00:00:00Z"))
    _green(env, "ci.yaml", "2026-09-02T00:00:00Z")

    sweep = _sweep(env)

    assert sweep["live"] == []
    assert sweep["paths_closed_by_later_green"] == {CI: "2026-09-02T00:00:00Z"}


def test_a_path_with_no_workflow_file_stays_live(env: dict[str, str]) -> None:
    """Dependabot's `dynamic/...` paths are not committed files, so the
    per-workflow endpoint 404s: those rows have no closure and stay live."""
    _page(env, "failure", 1, _red(300, "2026-09-01T00:00:00Z", path=DEPENDABOT))

    sweep = _sweep(env)

    assert [row["id"] for row in sweep["live"]] == [300]
    assert sweep["paths_closed_by_later_green"] == {}
