"""Which harness `agent_lifecycle` hands the turn to, once setup has passed."""

from __future__ import annotations

from pathlib import Path

import agent_lifecycle
import pytest
import sandbox_setup


@pytest.fixture(autouse=True)
def contained_sandbox_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` exports TEND_INSIDE_SANDBOX on the real environment.

    That is the point in production — `run_claude` reads it in-process and the
    runner's children inherit it — but every module guarding on it would then
    take the inside-sandbox branch for the rest of the pytest process, and this
    file sorts first in the directory. Registering the name here is what makes
    monkeypatch delete it on teardown; `delenv(raising=False)` registers
    nothing when the variable is absent, which it is.
    """
    monkeypatch.setenv("TEND_INSIDE_SANDBOX", "")


@pytest.fixture
def past_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both gates `main` clears before it reaches the harness branch."""
    monkeypatch.setattr(agent_lifecycle, "probe_boundary", lambda: None)
    monkeypatch.setattr(sandbox_setup, "main", lambda: 0)


def test_codex_runs_the_turn_through_the_runner(
    past_setup: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runner.py` dispatches on an exact argv and nothing else passes it `run`.

    The command reaches the runner from here rather than from the action's own
    steps, so a rename on either side would first fail in an adopter's job.
    """
    recorded = tmp_path / "argv"
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path({str(recorded)!r}).write_text(repr(sys.argv[1:]))\n"
        "raise SystemExit(7)\n"
    )
    monkeypatch.setenv("TEND_HARNESS", "codex")
    monkeypatch.setenv("TEND_CODEX_RUNNER", str(runner))

    assert agent_lifecycle.main() == 7
    assert recorded.read_text() == repr(["run"])


def test_setup_failure_reaches_no_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero `sandbox_setup` is the turn's exit code, not a harness boot."""
    monkeypatch.setattr(agent_lifecycle, "probe_boundary", lambda: None)
    monkeypatch.setattr(sandbox_setup, "main", lambda: 3)
    monkeypatch.setenv("TEND_HARNESS", "codex")
    monkeypatch.delenv("TEND_CODEX_RUNNER", raising=False)

    assert agent_lifecycle.main() == 3


def test_an_unknown_harness_is_not_silently_a_no_op(
    past_setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEND_HARNESS", "gemini")

    with pytest.raises(ValueError, match="gemini"):
        agent_lifecycle.main()
