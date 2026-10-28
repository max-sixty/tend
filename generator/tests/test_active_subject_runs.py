"""Tests for active_subject_runs.py — active dedicated subject-run candidates.

The notifications poll defers a thread whose subject a dedicated `tend-*`
workflow is already handling. Reading that wrong costs an outward action: the
poll answers a maintainer the queued `tend-mention` run was booted to answer,
and both sessions post as the same bot account.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests import GH_PREAMBLE, fake_bin, tool_path, uv_script

ACTIVE_SUBJECT_RUNS = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "tend-ci-runner"
    / "scripts"
    / "active_subject_runs.py"
)
SUBJECT_URL = "https://api.github.com/repos/owner/repo/pulls/4082"
TITLE = "fix: keep the resolved workspace root across retries"
OWN_RUN_ID = 999

# One runs file per status, so a fake `gh` answers `status=queued` with the
# queued runs and nothing else — exactly the partition the real API applies,
# and the one an `in_progress`-only query reads half of.
FAKE_GH = (
    GH_PREAMBLE
    + r"""
case "$*" in
  *"/actions/runs?status="*)
    args="$*"
    status="${args#*status=}"
    status="${status%%&*}"
    file="$RUNS_DIR/$status.json"
    [ -f "$file" ] && emit "$(cat "$file")" || emit '{"workflow_runs": []}'
    ;;
  *"/actions/runs/"*"/jobs"*)
    args="$*"
    rid="${args#*/actions/runs/}"
    rid="${rid%%/*}"
    file="$RUNS_DIR/jobs-$rid.json"
    [ -f "$file" ] && emit "$(cat "$file")" || emit '{"jobs": []}'
    ;;
  *"/pulls/"*) emit "$(cat "$SUBJECT_JSON")" ;;
  *) exit 1 ;;
esac
"""
)


def _ago(hours: float) -> str:
    """A timestamp *hours* back, against the wall clock the script reads."""
    moment = datetime.now(UTC) - timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(
    rid: int,
    name: str,
    status: str,
    title: str = TITLE,
    created_ago_hours: float = 0.25,
    updated_ago_hours: float | None = None,
) -> dict:
    """A run created *created_ago_hours* back, last touched *updated_ago_hours*.

    Defaults to a run GitHub has not moved since creating it — the shape the
    Actions API reports for one sitting in a queue, and for a stranded one.
    """
    updated = created_ago_hours if updated_ago_hours is None else updated_ago_hours
    return {
        "id": rid,
        "name": name,
        "path": f".github/workflows/{name}.yaml",
        "status": status,
        "display_title": title,
        "created_at": _ago(created_ago_hours),
        "updated_at": _ago(updated),
        "html_url": f"https://github.com/owner/repo/actions/runs/{rid}",
    }


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """Fake `gh` over a repository with no active runs at all."""
    bindir = fake_bin(tmp_path, gh=FAKE_GH)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    subject = tmp_path / "subject.json"
    subject.write_text(json.dumps({"title": TITLE, "number": 4082}))
    return {
        "PATH": tool_path(bindir),
        "GH_CALLS": str(tmp_path / "gh-calls.log"),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": str(OWN_RUN_ID),
        "RUNS_DIR": str(runs_dir),
        "SUBJECT_JSON": str(subject),
    }


def _stage(env: dict[str, str], status: str, *runs: dict) -> None:
    path = Path(env["RUNS_DIR"]) / f"{status}.json"
    path.write_text(json.dumps({"workflow_runs": list(runs)}))


def _subject_runs(env: dict[str, str]) -> list[dict]:
    result = subprocess.run(
        uv_script(ACTIVE_SUBJECT_RUNS, SUBJECT_URL),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_queued_run_is_a_subject_candidate(env: dict[str, str]) -> None:
    """The regression: a created-but-unstarted run still owns its subject.

    `queued` is a status of its own in the Actions API, so a dedicated run that
    has not been picked up yet answers no `status=in_progress` query. Reading
    that as unowned is what let the poll act on a subject `tend-mention` held.
    """
    _stage(env, "queued", _run(101, "tend-mention", "queued"))
    assert [run["id"] for run in _subject_runs(env)] == [101]


def test_every_non_terminal_status_is_queried(env: dict[str, str]) -> None:
    """Each non-terminal status needs its own request — one `status=` per call.

    The API answers an unrecognised status with an empty list rather than an
    error, so nothing but this assertion catches a mistyped or dropped one.
    """
    _subject_runs(env)
    calls = Path(env["GH_CALLS"]).read_text()
    for status in ("queued", "in_progress", "waiting", "requested", "pending"):
        assert f"status={status}&" in calls


def test_a_run_named_for_its_subject_keeps_its_workflow_identity(
    env: dict[str, str],
) -> None:
    """A relayed review's tend-mention run is named for the PR by `run-name`,
    which GitHub reports as the run's `name` too, so workflow identity comes
    from the workflow file."""
    run = _run(110, "tend-mention", "in_progress")
    run["name"] = TITLE
    _stage(env, "in_progress", run)
    assert [(run["id"], run["name"]) for run in _subject_runs(env)] == [
        (110, "tend-mention")
    ]


def test_a_tend_land_session_owns_the_pr_its_job_names(
    env: dict[str, str],
) -> None:
    """tend-land starts from a CI completion, so its run's title names no PR;
    each landing or repair session is a job named for its PR. A run whose
    active job works on this PR owns it, whatever the run is titled, so a
    notifications poll defers to it instead of starting a second repair."""
    mine = _run(111, "tend-land", "in_progress", title="ci")
    other = _run(112, "tend-land", "in_progress", title=TITLE)
    finished = _run(113, "tend-land", "in_progress", title="ci")
    _stage(env, "in_progress", mine, other, finished)
    jobs = {
        111: [
            {"name": "check", "status": "completed"},
            {"name": "repair #4082", "status": "in_progress"},
        ],
        # Titled like the PR, but its session works on another one.
        112: [{"name": "land #77", "status": "queued"}],
        113: [{"name": "land #4082", "status": "completed"}],
    }
    for rid, listed in jobs.items():
        (Path(env["RUNS_DIR"]) / f"jobs-{rid}.json").write_text(
            json.dumps({"jobs": listed})
        )
    assert [run["id"] for run in _subject_runs(env)] == [111]


def test_deployment_gated_run_is_a_subject_candidate(env: dict[str, str]) -> None:
    """A run held at an environment gate remains a candidate like a running one."""
    _stage(env, "waiting", _run(102, "tend-review", "waiting"))
    assert [run["id"] for run in _subject_runs(env)] == [102]


def test_in_progress_run_is_a_subject_candidate(env: dict[str, str]) -> None:
    _stage(env, "in_progress", _run(103, "tend-review", "in_progress"))
    assert _subject_runs(env) == [
        {
            "id": 103,
            "name": "tend-review",
            "path": ".github/workflows/tend-review.yaml",
            "status": "in_progress",
            "url": "https://github.com/owner/repo/actions/runs/103",
        }
    ]


@pytest.mark.parametrize("caller", ["tend-mention", "tend-notifications"])
def test_candidates_preserve_workflow_identity_without_inferring_concurrency(
    env: dict[str, str], caller: str
) -> None:
    """The same workflow may have independent groups after consumer overrides.

    A pending same-workflow run stays visible with its exact path, just as an
    independent workflow does. The caller needs those facts to interpret the
    actual concurrency topology; a notifications caller sees the same runs.
    """
    env["GITHUB_WORKFLOW_REF"] = (
        f"owner/repo/.github/workflows/{caller}.yaml@refs/heads/main"
    )
    mention = _run(1000, "tend-mention", "pending")
    mention["name"] = TITLE
    _stage(env, "pending", mention, _run(1001, "tend-review", "pending"))
    assert _subject_runs(env) == [
        {
            "id": 1000,
            "name": "tend-mention",
            "path": ".github/workflows/tend-mention.yaml",
            "status": "pending",
            "url": "https://github.com/owner/repo/actions/runs/1000",
        },
        {
            "id": 1001,
            "name": "tend-review",
            "path": ".github/workflows/tend-review.yaml",
            "status": "pending",
            "url": "https://github.com/owner/repo/actions/runs/1001",
        },
    ]


def test_own_run_and_other_subjects_are_not_candidates(env: dict[str, str]) -> None:
    """The poll does not defer to itself, to a non-Tend workflow, or to a run
    working some other subject."""
    _stage(
        env,
        "in_progress",
        _run(OWN_RUN_ID, "tend-notifications", "in_progress"),
        _run(104, "ci", "in_progress"),
        _run(105, "tend-review", "in_progress", title="some other pull request"),
    )
    assert _subject_runs(env) == []


def test_a_run_seen_twice_is_reported_once(env: dict[str, str]) -> None:
    """Status queries are separate requests, so a run that starts between two
    of them answers both. It is one candidate, not two."""
    _stage(env, "queued", _run(106, "tend-mention", "queued"))
    _stage(env, "in_progress", _run(106, "tend-mention", "in_progress"))
    candidates = _subject_runs(env)
    assert [run["id"] for run in candidates] == [106]
    assert candidates[0]["status"] == "queued"


def test_an_abandoned_run_is_not_a_candidate(env: dict[str, str]) -> None:
    """The regression on the other side: an owner that never finishes.

    GitHub strands runs non-terminal — created, no jobs, no further updates —
    and the Actions API keeps answering `status=queued` with them for good.
    Counting one as an owner defers its subject on every later poll, so the
    thread it holds is never handled by anything.
    """
    _stage(env, "queued", _run(107, "tend-mention", "queued", created_ago_hours=980))
    assert _subject_runs(env) == []


def test_a_run_queued_for_hours_remains_a_candidate(env: dict[str, str]) -> None:
    """The bound has to clear a real queue, not just a fast one.

    `tend-mention` waits behind its concurrency group for hours, and that run
    is the one this check exists to see.
    """
    _stage(env, "queued", _run(108, "tend-mention", "queued", created_ago_hours=20))
    assert [run["id"] for run in _subject_runs(env)] == [108]


def test_a_run_with_no_updated_at_remains_a_candidate(env: dict[str, str]) -> None:
    """An unreadable age is not evidence of abandonment.

    Deferring a poll costs a poll; dropping a live owner costs a second
    outward answer from the same bot account.
    """
    run = _run(109, "tend-mention", "queued")
    del run["updated_at"]
    _stage(env, "queued", run)
    assert [run["id"] for run in _subject_runs(env)] == [109]


def test_a_long_queued_run_that_started_remains_a_candidate(
    env: dict[str, str],
) -> None:
    """Age is measured from the last state change, not from creation.

    A run can queue for most of a day and then run out a job timeout hours
    long, putting it over a day past `created_at` while it is still working
    the subject. Dropping it there is the double answer the bound must not
    cause.
    """
    _stage(
        env,
        "in_progress",
        _run(
            110,
            "tend-mention",
            "in_progress",
            created_ago_hours=25,
            updated_ago_hours=2,
        ),
    )
    assert [run["id"] for run in _subject_runs(env)] == [110]


def test_a_queued_batch_owns_its_issues_before_jobs_exist(
    env: dict[str, str],
) -> None:
    """A batch can wait behind several long batches for more than a day.

    Its matrix has not expanded yet, so the submitted IDs are its ownership
    record; an age cutoff would admit a duplicate worker.
    """
    _stage(
        env,
        "pending",
        _run(
            201,
            "tend-triage",
            "pending",
            title="triage [77,4082]",
            created_ago_hours=72,
        ),
    )
    assert [run["id"] for run in _subject_runs(env)] == [201]


def test_batch_ownership_uses_issue_numbers_not_issue_titles(
    env: dict[str, str],
) -> None:
    """An issue title can itself look like another batch's run name."""
    Path(env["SUBJECT_JSON"]).write_text(
        json.dumps({"title": "triage [77]", "number": 4082})
    )
    _stage(
        env,
        "queued",
        _run(201, "tend-triage", "queued", title="triage [4082]"),
        _run(202, "tend-triage", "queued", title="triage [77]"),
    )
    assert [run["id"] for run in _subject_runs(env)] == [201]


def test_invalid_batches_do_not_hide_valid_or_legacy_subject_owners(
    env: dict[str, str],
) -> None:
    """A malformed dispatch fails selection, not every later ownership read.

    Runs created before the batch rollout still carry the issue title and
    retain ownership until they finish.
    """
    _stage(
        env,
        "queued",
        _run(201, "tend-triage", "queued", title="triage oops"),
        _run(202, "tend-triage", "queued", title="triage [true]"),
        _run(203, "tend-triage", "queued", title="triage [4082]"),
        _run(204, "tend-triage", "queued", title=TITLE),
    )
    assert [run["id"] for run in _subject_runs(env)] == [203, 204]


def test_batch_releases_completed_subjects_while_other_legs_continue(
    env: dict[str, str],
) -> None:
    _stage(
        env,
        "in_progress",
        _run(201, "tend-triage", "in_progress", title="triage [4082,77]"),
        _run(202, "tend-triage", "in_progress", title="triage [78,4082]"),
    )
    for rid, jobs in {
        201: [
            {"name": "triage #4082", "status": "completed"},
            {"name": "triage #77", "status": "in_progress"},
        ],
        202: [
            {"name": "select", "status": "completed"},
            {"name": "triage #78", "status": "in_progress"},
            {"name": "triage #4082", "status": "queued"},
        ],
    }.items():
        (Path(env["RUNS_DIR"]) / f"jobs-{rid}.json").write_text(
            json.dumps({"jobs": jobs})
        )
    assert [run["id"] for run in _subject_runs(env)] == [202]


@pytest.mark.parametrize("status", ["queued", "completed"])
def test_batch_ownership_reads_every_run_and_job_page(
    env: dict[str, str], status: str
) -> None:
    """The owner and its subject leg both fall beyond the first API page.

    The fake models gh's paginated object responses. Reading just one page
    either misses the owner entirely or keeps an already completed subject.
    """
    pages = [
        {
            "workflow_runs": [
                _run(i, "tend-mention", "in_progress", title="another subject")
                for i in range(100, 200)
            ]
        },
        {
            "workflow_runs": [
                _run(
                    201,
                    "tend-triage",
                    "in_progress",
                    title=f"triage {json.dumps([*range(100, 200), 4082])}",
                )
            ]
        },
    ]
    runs_dir = Path(env["RUNS_DIR"])
    (runs_dir / "in_progress.json").write_text(
        "\n".join(json.dumps(page) for page in pages)
    )
    job_pages = [
        {
            "jobs": [
                {"name": f"triage #{i}", "status": "completed"} for i in range(100, 200)
            ]
        },
        {"jobs": [{"name": "triage #4082", "status": status}]},
    ]
    (runs_dir / "jobs-201.json").write_text(
        "\n".join(json.dumps(page) for page in job_pages)
    )
    expected = [201] if status == "queued" else []
    assert [run["id"] for run in _subject_runs(env)] == expected
    calls = Path(env["GH_CALLS"]).read_text().splitlines()
    listings = [call for call in calls if "/actions/runs" in call]
    assert all("--paginate" in call and "--slurp" in call for call in listings)
