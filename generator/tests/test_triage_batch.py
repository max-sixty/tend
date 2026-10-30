"""Exercise triage transport and live selection through the runtime CLI."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from tend.cli import main
from tend.runtime.shared import triage_batch

from _fakes import FakeGh, GithubFiles


@pytest.fixture(autouse=True)
def batch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("BOOKKEEPING_LABELS", '["tend-outage"]')


@pytest.mark.parametrize("numbers", [[], [7], [7, 7, 8], list(range(1, 101))])
def test_selector_expands_exact_issue_ids(
    numbers: list[int],
    fake_gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    fake_gh.respond("api", with_={"state": "open", "labels": []})
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "select"],
        env={"ISSUES": json.dumps(numbers)},
    )
    assert result.exit_code == 0, result.output
    expected = list(dict.fromkeys(numbers))
    assert json.loads(github_files.outputs()["issues"]) == expected
    assert fake_gh.calls == [("api", f"repos/owner/repo/issues/{n}") for n in expected]


@pytest.mark.parametrize(
    "raw", ['"7"', "[true]", "[1.2]", "[0]", "[-1]", "{}", "null", "oops"]
)
@pytest.mark.parametrize("operation", ["select", "dispatch"])
def test_invalid_selection_fails_before_github(
    raw: str,
    operation: str,
    fake_gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    arguments = ["runtime", "triage-batch", operation]
    if operation == "dispatch":
        arguments.append(raw)
    result = CliRunner().invoke(main, arguments, env={"ISSUES": raw})
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert github_files.outputs() == {}
    assert fake_gh.calls == []


def test_selector_rejects_raw_input_that_would_truncate(
    fake_gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    # Even a one-issue array loses its closing bracket when padded in a run name.
    # Compacting it here would conceal the already-truncated discovery record.
    raw = "[7]" + " " * 503
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "select"],
        env={"ISSUES": raw},
    )
    assert result.exit_code == 1
    assert "512-character limit" in result.output
    assert fake_gh.calls == []
    assert github_files.outputs() == {}


def test_queued_selection_rechecks_live_issues(
    fake_gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    records = {
        1: {"state": "closed", "labels": []},
        2: {"state": "open", "labels": [], "pull_request": {}},
        3: {"state": "open", "labels": [{"name": "tend-outage"}]},
        4: {"state": "open", "labels": [{"name": "tend-ci-failure"}]},
    }
    for number, record in records.items():
        fake_gh.respond("api", f"repos/owner/repo/issues/{number}", with_=record)
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "select"],
        env={"ISSUES": "[1,2,3,4]"},
    )
    assert result.exit_code == 0, result.output
    assert github_files.outputs() == {"issues": "[4]"}


def test_unreadable_issue_fails_without_partial_matrix(
    fake_gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    fake_gh.respond("api", with_={"state": "open", "labels": []})
    fake_gh.respond("api", "repos/owner/repo/issues/8", with_=9)
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "select"],
        env={"ISSUES": "[7,8]"},
    )
    assert result.exit_code == 1
    assert "failed (exit 9)" in result.output
    assert github_files.outputs() == {}


@pytest.mark.parametrize(
    "numbers",
    [[], [7], [9, 7, 9, 8], list(range(1, 101)), list(range(100000, 100256))],
)
def test_dispatch_preserves_order_and_fits_provider_limits(
    numbers: list[int],
    fake_gh: FakeGh,
) -> None:
    url = "https://github.com/owner/repo/actions/runs/123\n"
    fake_gh.respond("workflow", "run", with_=url)
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "dispatch", json.dumps(numbers)],
    )
    assert result.exit_code == 0, result.output
    batches = []
    for call in fake_gh.calls:
        assert call[:-1] == (
            "workflow",
            "run",
            "tend-triage.yaml",
            "--repo",
            "owner/repo",
            "-f",
        )
        raw = call[-1].removeprefix("issues=")
        batch = json.loads(raw)
        assert raw == json.dumps(batch, separators=(",", ":"))
        assert len("triage " + raw) <= 512
        assert 0 < len(batch) <= 256
        batches.extend(batch)
    assert batches == list(dict.fromkeys(numbers))
    assert result.output == url * len(fake_gh.calls)
    if len(numbers) <= 100:
        assert len(fake_gh.calls) == bool(numbers)
    else:
        assert len(fake_gh.calls) > 1


def test_dispatch_boundary_and_oversized_number(
    fake_gh: FakeGh,
    github_files: GithubFiles,
) -> None:
    fake_gh.respond("workflow", "run", with_="")
    # An exact 512-character run name is valid; one more digit cannot be sent.
    fits = 10**502
    raw = json.dumps([fits])
    assert len("triage " + raw) == 512
    result = CliRunner().invoke(main, ["runtime", "triage-batch", "dispatch", raw])
    assert result.exit_code == 0, result.output
    assert len(fake_gh.calls) == 1
    fake_gh.calls.clear()
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "dispatch", json.dumps([7, fits * 10])],
    )
    assert result.exit_code == 1
    assert "run-name limit" in result.output
    assert fake_gh.calls == []
    fake_gh.respond("api", with_={"state": "open", "labels": []})
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "select"],
        env={"ISSUES": raw},
    )
    assert result.exit_code == 0, result.output
    assert json.loads(github_files.outputs()["issues"]) == [fits]


def test_dispatch_stops_on_first_failed_request(fake_gh: FakeGh) -> None:
    numbers = list(range(100000, 100256))
    batches = triage_batch.dispatch_batches(json.dumps(numbers))
    fake_gh.respond("workflow", "run", with_="first-run-url\n")
    fake_gh.respond(
        "workflow",
        "run",
        "tend-triage.yaml",
        "--repo",
        "owner/repo",
        "-f",
        f"issues={batches[1]}",
        with_=9,
    )
    result = CliRunner().invoke(
        main,
        ["runtime", "triage-batch", "dispatch", json.dumps(numbers)],
    )
    assert result.exit_code == 1
    assert "first-run-url" in result.output
    assert "failed (exit 9)" in result.output
    assert len(fake_gh.calls) == 2
