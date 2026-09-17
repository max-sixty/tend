"""Behavior tests for outage enrichment and its public-comment dedup."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests import GH_PREAMBLE, fake_bin, tool_path, uv_script

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "tend-ci-runner"
    / "scripts"
    / "enrich_tend_outage_issues.py"
)
RUN_ID = "11"
JOB_ID = "101"
EXIT_ONLY = [
    {"annotation_level": "failure", "message": "Process completed with exit code 1."}
]


def _log(*lines: str, job: str = "test", step: str = "Nightly suite") -> str:
    return "".join(
        f"{job}\t{step}\t2026-09-06T08:0{i}:00.1234567Z {line}\n"
        for i, line in enumerate(lines)
    )


PYTEST_TAIL = _log(
    "FAILED tests/test_render_navigation.py::test_holding_a_key_repeats - AssertionError",
    "Actual value: 0",
    "==== 1 failed, 2021 passed, 6 skipped in 2053.74s (0:34:13) ====",
    "##[error]Process completed with exit code 1.",
)
COLOURED_TAIL = _log(
    "shellcheck.................................................^[[42mPassed^[[m",
    "^[[31mFAILED^[[0m tests/test_sandbox.py::test_proxy_env",
    "##[error]Process completed with exit code 1.",
)
BOM_TAIL = (
    "test\tRun the action\t\ufeff2026-09-06T08:00:00.1234567Z "
    "curl: (35) Recv failure\n" + _log("##[error]Process completed with exit code 35.")
)

FAKE_GH = (
    GH_PREAMBLE
    + r"""
case "$*" in
  "repo view"*) emit '{"nameWithOwner":"owner/repo"}' ;;
  "issue list"*) emit '[{"number":7}]' ;;
  "issue view"*) emit "$(cat "$ISSUE_JSON")" ;;
  *"/jobs"*)
    run_id=$(printf '%s' "$*" | sed -n 's|.*/actions/runs/\([0-9]*\)/jobs.*|\1|p')
    override="$(dirname "$JOBS_JSON")/jobs-$run_id.json"
    if [ -f "$override" ]; then emit "$(cat "$override")"; else emit "$(cat "$JOBS_JSON")"; fi
    ;;
  *"/annotations"*)
    job_id=$(printf '%s' "$*" | sed -n 's|.*/check-runs/\([0-9]*\)/annotations.*|\1|p')
    override="$(dirname "$ANNOTATIONS_JSON")/annotations-$job_id.json"
    if [ -f "$override" ]; then emit "$(cat "$override")"; else emit "$(cat "$ANNOTATIONS_JSON")"; fi
    ;;
  "run view"*--log-failed*)
    if [ -n "${LOG_FAILS:-}" ]; then exit 1; fi
    case "$*" in
      *--attempt*) cat "$LOG_TXT" ;;
      *) cat "${LATEST_LOG_TXT:-$LOG_TXT}" ;;
    esac
    ;;
  "issue comment"*)
    prev=""
    for arg in "$@"; do
      [ "$prev" = "-F" ] && cp "$arg" "$POSTED_BODY"
      prev="$arg"
    done
    ;;
  *) exit 1 ;;
esac
"""
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bindir = fake_bin(tmp_path, gh=FAKE_GH)
    issue = {
        "body": f"Failed https://github.com/owner/repo/actions/runs/{RUN_ID}",
        "comments": [
            {
                "body": "Old https://github.com/owner/repo/actions/runs/10\n"
                "<!-- enriched-run:10 -->"
            }
        ],
    }
    jobs = {"jobs": [{"id": int(JOB_ID), "name": "tests", "conclusion": "failure"}]}
    annotations = [
        {"annotation_level": "failure", "message": "assertion failed"},
        {"annotation_level": "failure", "message": "Process completed with exit 1"},
    ]
    for name, value in (
        ("issue.json", issue),
        ("jobs.json", jobs),
        ("annotations.json", annotations),
    ):
        (tmp_path / name).write_text(json.dumps(value))
    log = tmp_path / "log.txt"
    log.write_text(PYTEST_TAIL)
    return {
        "PATH": tool_path(bindir),
        "GH_CALLS": str(tmp_path / "gh-calls.log"),
        "ISSUE_JSON": str(tmp_path / "issue.json"),
        "JOBS_JSON": str(tmp_path / "jobs.json"),
        "ANNOTATIONS_JSON": str(tmp_path / "annotations.json"),
        "POSTED_BODY": str(tmp_path / "posted.md"),
        "LOG_TXT": str(log),
    }


def _run(env: dict[str, str]) -> str:
    result = subprocess.run(
        uv_script(SCRIPT), env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    posted = Path(env["POSTED_BODY"])
    return posted.read_text() if posted.exists() else ""


def test_posts_one_batch_for_only_unenriched_runs(env: dict[str, str]) -> None:
    body = _run(env)

    assert body == (
        "### [Run 11](https://github.com/owner/repo/actions/runs/11)\n\n"
        "#### tests\n\n```\nassertion failed\n```\n\n"
        "<!-- enriched-run:11 -->\n"
    )
    calls = Path(env["GH_CALLS"]).read_text()
    assert "/actions/runs/10/jobs" not in calls


def test_a_plain_non_zero_exit_is_enriched_from_the_log(
    env: dict[str, str],
) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))

    body = _run(env)

    assert "test_holding_a_key_repeats" in body
    assert "1 failed, 2021 passed" in body
    assert "No failure details could be extracted." not in body
    assert f"<!-- enriched-run:{RUN_ID} -->" in body


def test_the_log_prefix_is_stripped(env: dict[str, str]) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))

    body = _run(env)

    assert "FAILED tests/test_render_navigation.py" in body
    assert "2026-09-06T08:00:00.1234567Z" not in body


def test_annotations_win_and_the_log_is_not_fetched(env: dict[str, str]) -> None:
    body = _run(env)

    assert "assertion failed" in body
    assert "log tail" not in body
    assert "run view" not in Path(env["GH_CALLS"]).read_text()


def test_annotation_fence_outgrows_agent_markdown(env: dict[str, str]) -> None:
    message = (
        "The failing command was:\n\n`````markdown\n```bash\nuv run pytest\n```\n"
        "`````\n\nRe-run it locally."
    )
    Path(env["ANNOTATIONS_JSON"]).write_text(
        json.dumps([{"annotation_level": "failure", "message": message}])
    )

    body = _run(env)

    assert f"#### tests\n\n``````\n{message}\n``````" in body
    assert body.index("Re-run it locally.") < body.index(
        f"<!-- enriched-run:{RUN_ID} -->"
    )


def test_a_run_with_no_recoverable_detail_still_records_the_marker(
    env: dict[str, str],
) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))
    Path(env["LOG_TXT"]).write_text("")

    body = _run(env)

    assert "No failure details could be extracted." in body
    assert f"<!-- enriched-run:{RUN_ID} -->" in body


def test_a_rerun_that_went_green_is_enriched_from_the_failed_attempt(
    env: dict[str, str],
) -> None:
    # The row is recorded when the run fails; a rerun that then succeeds leaves
    # the default `latest` job view with nothing failed and no failed log, so
    # the only copy of the diagnosis lives on the earlier attempt.
    Path(env["JOBS_JSON"]).write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": int(JOB_ID),
                        "name": "review",
                        "conclusion": "failure",
                        "run_attempt": 1,
                    },
                    {
                        "id": 102,
                        "name": "review",
                        "conclusion": "success",
                        "run_attempt": 2,
                    },
                ]
            }
        )
    )
    Path(env["LOG_TXT"]).write_text("")

    body = _run(env)

    assert "filter=all" in Path(env["GH_CALLS"]).read_text()
    assert "#### review (attempt 1)\n\n```\nassertion failed\n```" in body
    assert "No failure details could be extracted." not in body


def test_a_rerun_that_went_green_reads_the_failed_attempt_log(
    env: dict[str, str], tmp_path: Path
) -> None:
    # A transient failure annotates only `Process completed with exit code N`,
    # so its diagnosis lives solely in the log — and `--log-failed` reads the
    # rerun's green attempt unless it is told which attempt failed.
    Path(env["JOBS_JSON"]).write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": int(JOB_ID),
                        "name": "review",
                        "conclusion": "failure",
                        "run_attempt": 1,
                    },
                    {
                        "id": 102,
                        "name": "review",
                        "conclusion": "success",
                        "run_attempt": 2,
                    },
                ]
            }
        )
    )
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))
    Path(env["LOG_TXT"]).write_text(BOM_TAIL)
    latest = tmp_path / "latest-log.txt"
    latest.write_text("")
    env["LATEST_LOG_TXT"] = str(latest)

    body = _run(env)

    assert "--attempt 1" in Path(env["GH_CALLS"]).read_text()
    assert "curl: (35) Recv failure" in body
    assert "No failure details could be extracted." not in body


def test_a_run_with_no_readable_jobs_still_reads_the_latest_log(
    env: dict[str, str],
) -> None:
    # With the jobs read unavailable there is no attempt to name, so the log
    # source stays on the latest attempt rather than losing its one fallback.
    Path(env["JOBS_JSON"]).write_text("not json")

    body = _run(env)

    assert "--attempt" not in Path(env["GH_CALLS"]).read_text()
    assert "1 failed, 2021 passed" in body


def test_a_single_attempt_job_heading_carries_no_attempt(env: dict[str, str]) -> None:
    body = _run(env)

    assert "#### tests\n\n" in body
    assert "attempt" not in body


def test_an_unavailable_log_does_not_abort_the_batch(env: dict[str, str]) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))
    env["LOG_FAILS"] = "1"

    body = _run(env)

    assert "No failure details could be extracted." in body


def test_terminal_colour_escapes_are_stripped(env: dict[str, str]) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))
    Path(env["LOG_TXT"]).write_text(COLOURED_TAIL)

    body = _run(env)

    assert "^[" not in body
    assert "shellcheck.................................................Passed" in body
    assert "FAILED tests/test_sandbox.py::test_proxy_env" in body


def test_log_fence_outgrows_fenced_output(env: dict[str, str]) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))
    Path(env["LOG_TXT"]).write_text(
        _log(
            "````",
            "diagnostic",
            "````",
            "##[error]Process completed with exit code 1.",
        )
    )

    body = _run(env)

    assert "#### log tail\n\n`````\n````\ndiagnostic\n````\n" in body
    assert f"<!-- enriched-run:{RUN_ID} -->" in body


def test_the_step_boundary_bom_does_not_defeat_the_timestamp_strip(
    env: dict[str, str],
) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))
    Path(env["LOG_TXT"]).write_text(BOM_TAIL)

    body = _run(env)

    assert "curl: (35) Recv failure" in body
    assert "2026-09-06T08:00:00.1234567Z" not in body
    assert "\ufeff" not in body


def _fence_lines(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith("```")]


def _many_runs(env: dict[str, str], count: int) -> list[str]:
    runs = [str(int(RUN_ID) + i) for i in range(count)]
    Path(env["ISSUE_JSON"]).write_text(
        json.dumps(
            {
                "body": "\n".join(
                    f"https://github.com/owner/repo/actions/runs/{run}" for run in runs
                ),
                "comments": [],
            }
        )
    )
    return runs


def test_an_oversized_batch_is_truncated_between_runs(env: dict[str, str]) -> None:
    runs = _many_runs(env, 6)
    Path(env["ANNOTATIONS_JSON"]).write_text(json.dumps(EXIT_ONLY))
    Path(env["LOG_TXT"]).write_text(
        _log(*(["x" * 600] * 30), "##[error]Process completed with exit code 1.")
    )

    body = _run(env)

    assert len(body.encode()) < 65_536
    assert "_Truncated" in body
    assert f"<!-- enriched-run:{runs[0]} -->" in body
    assert f"<!-- enriched-run:{runs[-1]} -->" not in body
    assert len(_fence_lines(body)) % 2 == 0
    for run in runs:
        if f"### [Run {run}]" in body:
            assert f"<!-- enriched-run:{run} -->" in body
    assert body.rstrip().endswith(
        "_Truncated; the remaining runs are enriched by a later batch._"
    )


def test_one_huge_annotation_cannot_fill_the_body(env: dict[str, str]) -> None:
    Path(env["ANNOTATIONS_JSON"]).write_text(
        json.dumps(
            [
                {
                    "annotation_level": "failure",
                    "message": "E501 line too long " * 4000,
                },
                {
                    "annotation_level": "failure",
                    "message": "\n".join(["F401"] * 4000),
                },
            ]
        )
    )

    body = _run(env)

    assert len(body.encode()) < 65_536
    assert f"<!-- enriched-run:{RUN_ID} -->" in body
    assert "E501 line too long" in body
    assert len(_fence_lines(body)) % 2 == 0


def test_a_matrix_of_failed_jobs_cannot_fill_the_body(env: dict[str, str]) -> None:
    Path(env["JOBS_JSON"]).write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": int(JOB_ID) + i,
                        "name": f"test ({i})",
                        "conclusion": "failure",
                    }
                    for i in range(40)
                ]
            }
        )
    )
    Path(env["ANNOTATIONS_JSON"]).write_text(
        json.dumps(
            [
                {
                    "annotation_level": "failure",
                    "message": "\n".join(["y" * 600] * 30),
                }
            ]
        )
    )

    body = _run(env)

    assert len(body.encode()) < 65_536
    assert f"<!-- enriched-run:{RUN_ID} -->" in body
    assert "_Remaining failed jobs omitted._" in body
    assert len(_fence_lines(body)) % 2 == 0


def _jobs_for(env: dict[str, str], run_id: str, jobs: dict[str, object]) -> None:
    """Serve a run-specific jobs response instead of the shared fixture one."""
    path = Path(env["JOBS_JSON"]).with_name(f"jobs-{run_id}.json")
    path.write_text(json.dumps(jobs))


def _jobs_pages_for(
    env: dict[str, str], run_id: str, pages: list[dict[str, object]]
) -> None:
    """Serve one run's jobs as the page stream ``gh api --paginate`` emits."""
    path = Path(env["JOBS_JSON"]).with_name(f"jobs-{run_id}.json")
    path.write_text("".join(json.dumps(page) for page in pages))


def _annotations_for(env: dict[str, str], job_id: int, messages: list[str]) -> None:
    """Serve one job's annotations instead of the shared fixture ones."""
    path = Path(env["ANNOTATIONS_JSON"]).with_name(f"annotations-{job_id}.json")
    path.write_text(
        json.dumps(
            [
                {"annotation_level": "failure", "message": message}
                for message in messages
            ]
        )
    )


GREEN_JOBS: dict[str, object] = {
    "jobs": [{"id": 201, "name": "deploy", "conclusion": "success", "run_attempt": 1}]
}


def test_a_green_baseline_run_is_not_enriched(env: dict[str, str]) -> None:
    # A diagnosis cites green runs as its comparison baseline. They never
    # failed, so the "could not extract" sentence would misreport them as
    # undiagnosable failures, and a marker would block a later rerun-to-red.
    green = "12"
    Path(env["ISSUE_JSON"]).write_text(
        json.dumps(
            {
                "body": (
                    f"Failed https://github.com/owner/repo/actions/runs/{RUN_ID}\n"
                    f"Baseline https://github.com/owner/repo/actions/runs/{green}"
                ),
                "comments": [],
            }
        )
    )
    _jobs_for(env, green, GREEN_JOBS)

    body = _run(env)

    assert f"### [Run {green}]" not in body
    assert f"<!-- enriched-run:{green} -->" not in body
    assert "No failure details could be extracted." not in body
    assert f"<!-- enriched-run:{RUN_ID} -->" in body


def test_an_issue_citing_only_green_runs_posts_nothing(env: dict[str, str]) -> None:
    _jobs_for(env, RUN_ID, GREEN_JOBS)

    assert _run(env) == ""
    assert "run view" not in Path(env["GH_CALLS"]).read_text()


def test_the_jobs_read_walks_every_page(env: dict[str, str]) -> None:
    # The green-run guard reads rows with no failure among them as a run that
    # never failed, so a read stopping at one page would drop a wide matrix's
    # real failure.
    _run(env)

    assert (
        f"api --paginate repos/owner/repo/actions/runs/{RUN_ID}"
        "/jobs?filter=all&per_page=100" in Path(env["GH_CALLS"]).read_text()
    )


def test_a_failure_on_a_later_jobs_page_is_enriched(env: dict[str, str]) -> None:
    # GitHub caps the page at 100 rows while a matrix can create far more, so
    # the failed row routinely sits behind the first page.
    _jobs_pages_for(
        env,
        RUN_ID,
        [
            GREEN_JOBS,
            {
                "jobs": [
                    {
                        "id": int(JOB_ID),
                        "name": "tests",
                        "conclusion": "failure",
                        "run_attempt": 1,
                    }
                ]
            },
        ],
    )

    body = _run(env)

    assert "assertion failed" in body
    assert f"<!-- enriched-run:{RUN_ID} -->" in body


def test_a_cancelled_run_is_still_enriched(env: dict[str, str]) -> None:
    # `ci-fix` fires on cancelled runs as well as failed ones, so its tracker
    # cites runs whose jobs never carry `conclusion: failure`. Reading those as
    # green would drop the diagnosis with no section and no marker.
    _jobs_for(
        env,
        RUN_ID,
        {
            "jobs": [
                {
                    "id": int(JOB_ID),
                    "name": "tests",
                    "conclusion": "cancelled",
                    "run_attempt": 1,
                }
            ]
        },
    )
    # `gh run view --log-failed` selects failed steps, and a cancelled job has
    # none, so the real CLI returns nothing here: the annotation naming the
    # cancelling request is the whole diagnosis.
    Path(env["LOG_TXT"]).write_text("")
    Path(env["ANNOTATIONS_JSON"]).write_text(
        json.dumps(
            [
                {
                    "annotation_level": "failure",
                    "message": "Canceling since a higher priority request exists",
                }
            ]
        )
    )

    body = _run(env)

    assert "Canceling since a higher priority request exists" in body
    assert "No failure details could be extracted." not in body
    assert f"<!-- enriched-run:{RUN_ID} -->" in body


def test_a_fail_fast_matrix_reports_only_the_job_that_failed(
    env: dict[str, str],
) -> None:
    # Fail-fast cancels the failed job's siblings, and each cancelled row
    # annotates "The operation was canceled." Reporting those alongside the one
    # real error buries it and spends the run's byte budget on noise.
    _jobs_for(
        env,
        RUN_ID,
        {
            "jobs": [
                {"id": 301, "name": "py-3.12", "conclusion": "cancelled"},
                {"id": int(JOB_ID), "name": "py-3.13", "conclusion": "failure"},
            ]
        },
    )

    body = _run(env)

    assert "#### py-3.12" not in body
    assert "#### py-3.13" in body


def test_a_cancelled_matrix_reports_its_annotation_once(env: dict[str, str]) -> None:
    # A run-level cancellation annotates every job with the same message, so a
    # section per job repeats one diagnosis until it fills the run's byte
    # budget and crowds the other runs into a later batch.
    _jobs_for(
        env,
        RUN_ID,
        {
            "jobs": [
                {"id": 400 + i, "name": f"Analyze ({i})", "conclusion": "cancelled"}
                for i in range(4)
            ]
        },
    )
    Path(env["ANNOTATIONS_JSON"]).write_text(
        json.dumps(
            [
                {
                    "annotation_level": "failure",
                    "message": "The run was canceled by @github-advanced-security[bot].",
                }
            ]
        )
    )

    body = _run(env)

    assert body.count("The run was canceled") == 1


def test_a_timed_out_job_survives_its_cancelled_siblings(env: dict[str, str]) -> None:
    # A timeout cancels the rest of the run, so the job that exceeded the limit
    # sits among cancelled siblings and is the only one whose annotation names
    # it. Deduping the repeated cancellation must not take it with them.
    _jobs_for(
        env,
        RUN_ID,
        {
            "jobs": [
                {"id": 401, "name": "py-3.12", "conclusion": "cancelled"},
                {"id": 402, "name": "py-3.13", "conclusion": "cancelled"},
                {"id": int(JOB_ID), "name": "py-3.14", "conclusion": "timed_out"},
            ]
        },
    )
    for job_id in (401, 402):
        _annotations_for(env, job_id, ["The operation was canceled."])
    _annotations_for(
        env,
        int(JOB_ID),
        ["The job running on runner ubuntu-24.04 has exceeded the maximum time"],
    )

    body = _run(env)

    assert "has exceeded the maximum time" in body
    assert body.count("The operation was canceled.") == 1


def test_a_cancelled_sibling_carries_a_run_that_never_started(
    env: dict[str, str],
) -> None:
    # A job cancelled before it started carries no annotation at all, so the
    # run's only diagnosis sits on the sibling that did start. Dropping the
    # repeated message rather than choosing one job per conclusion is what
    # keeps that sibling's section — otherwise the run renders the "could not
    # extract" sentence and takes its durable marker with nothing in it.
    _jobs_for(
        env,
        RUN_ID,
        {
            "jobs": [
                {"id": 501, "name": "lint", "conclusion": "cancelled"},
                {"id": 502, "name": "test", "conclusion": "cancelled"},
            ]
        },
    )
    _annotations_for(
        env, 501, ["Canceling since a higher priority waiting request exists"]
    )
    _annotations_for(env, 502, [])
    Path(env["LOG_TXT"]).write_text("")

    body = _run(env)

    assert "Canceling since a higher priority waiting request exists" in body
    assert "No failure details could be extracted." not in body


def test_a_run_whose_attempts_are_still_going_is_left_unmarked(
    env: dict[str, str],
) -> None:
    # A marker is durable, so marking a run still in flight would block the
    # enrichment for good. Leave it for a later nightly instead.
    _jobs_for(
        env,
        RUN_ID,
        {"jobs": [{"id": int(JOB_ID), "name": "tests", "conclusion": None}]},
    )

    assert _run(env) == ""


def test_an_unreadable_jobs_response_is_not_read_as_green(
    env: dict[str, str],
) -> None:
    # An API failure yields no rows, which is not evidence the run passed.
    Path(env["JOBS_JSON"]).write_text("not json")
    Path(env["LOG_TXT"]).write_text("")

    body = _run(env)

    assert "No failure details could be extracted." in body
    assert f"<!-- enriched-run:{RUN_ID} -->" in body


def test_an_issue_with_nothing_new_posts_nothing(env: dict[str, str]) -> None:
    Path(env["ISSUE_JSON"]).write_text(
        json.dumps(
            {
                "body": f"https://github.com/owner/repo/actions/runs/{RUN_ID}",
                "comments": [{"body": f"<!-- enriched-run:{RUN_ID} -->"}],
            }
        )
    )

    assert _run(env) == ""
