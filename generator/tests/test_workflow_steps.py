"""Workflow decisions exercised through the operations their CLI invokes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from tend.runtime.shared import _common, workflow_steps


@pytest.fixture
def output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    return path


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [("", ""), ("2026-01-02T12:00:00Z", "45"), ("2026-01-02T04:00:00-08:00", "45")],
)
def test_delay_uses_captured_job_start(
    timestamp: str, expected: str, output: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVENT_TS", timestamp)
    monkeypatch.setenv("TEND_JOB_STARTED_AT", "1767355245")
    assert workflow_steps.queue_delay() == 0
    assert output.read_text() == f"seconds={expected}\n"


@pytest.mark.parametrize("live", ["expected-head", "moved-head"])
def test_current_head(output: Path, monkeypatch: pytest.MonkeyPatch, live: str) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("PR", "12")
    monkeypatch.setenv("SHA", "expected-head")

    def gh(*args: str, **kwargs: object) -> str:
        assert args == ("api", "repos/owner/repo/pulls/12")
        return json.dumps({"head": {"sha": live}})

    monkeypatch.setattr(_common, "gh", gh)
    assert workflow_steps.current_head() == 0
    assert output.read_text() == f"current={str(live == 'expected-head').lower()}\n"


@pytest.mark.parametrize("own_reaction", [False, True])
def test_reaction_removal_paginates_and_preserves_human_reactions(
    monkeypatch: pytest.MonkeyPatch, own_reaction: bool
) -> None:
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("TARGET", "issues/comments/12")
    monkeypatch.setenv("BOT_NAME", "the-bot")
    endpoint = "repos/owner/repo/issues/comments/12/reactions"
    calls = []

    def gh(*args: str, **kwargs: object) -> str:
        calls.append(args)
        if args[1] == "--paginate":
            assert args == (
                "api",
                "--paginate",
                "--slurp",
                f"{endpoint}?content=eyes&per_page=100",
            )
            return json.dumps(
                [
                    [{"id": 1, "user": {"login": "human"}}],
                    [
                        {
                            "id": 2,
                            "user": {
                                "login": "the-bot" if own_reaction else "another-human"
                            },
                        }
                    ],
                ]
            )
        assert args == ("api", "-X", "DELETE", f"{endpoint}/2", "--silent")
        return ""

    monkeypatch.setattr(_common, "gh", gh)
    assert workflow_steps.reaction(remove=True) == 0
    assert len(calls) == (2 if own_reaction else 1)


@pytest.mark.parametrize("remove", [False, True])
def test_reaction_api_failure_does_not_fail_the_work(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], remove: bool
) -> None:
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("TARGET", "issues/12")
    monkeypatch.setenv("BOT_NAME", "the-bot")

    def gh(*args: str, **kwargs: object) -> str:
        raise subprocess.CalledProcessError(1, ["gh", *args])

    monkeypatch.setattr(_common, "gh", gh)
    assert workflow_steps.reaction(remove=remove) == 0
    assert "::warning::" in capsys.readouterr().out


def test_installed_workflow_cli(
    installed_runtime: tuple[Path, Path], output: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python, _ = installed_runtime
    monkeypatch.setenv("EVENT_TS", "")
    result = subprocess.run(
        [str(python), "-I", "-m", "tend", "runtime", "queue-delay"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "seconds=\n"
