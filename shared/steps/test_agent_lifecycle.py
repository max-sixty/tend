"""Which harness `agent_lifecycle` hands the turn to, once setup has passed."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import agent_lifecycle
import event_checkout
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


@pytest.fixture(autouse=True)
def before_the_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything `main` runs inside the view before it reaches a harness."""
    for name, value in (
        ("GITHUB_WORKSPACE", "/workspace"),
        ("BOT_NAME", "tend-bot"),
        ("BOT_ID", "42"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(agent_lifecycle, "probe_boundary", lambda _workspace: None)
    monkeypatch.setattr(agent_lifecycle, "configure_git", lambda _login, _id: None)
    monkeypatch.setattr(event_checkout, "main", lambda: 0)


@pytest.fixture
def past_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The last gate `main` clears before it reaches the harness branch."""
    monkeypatch.setattr(sandbox_setup, "main", lambda: 0)


def test_codex_runs_the_turn_through_the_runner(
    past_setup: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runner.py` dispatches on an exact argv and nothing else passes it `run`.

    The command reaches the runner from here rather than from the action's own
    steps, so a rename on either side would first fail in a consumer's job.
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
    monkeypatch.setattr(sandbox_setup, "main", lambda: 3)
    monkeypatch.setenv("TEND_HARNESS", "codex")
    monkeypatch.delenv("TEND_CODEX_RUNNER", raising=False)

    assert agent_lifecycle.main() == 3


def test_a_missing_input_fails_by_name_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A launch that dropped a variable is a wiring bug in tend, not the turn's.

    Read deep inside, it would surface as a bare ``KeyError`` from whichever
    step first reached it, after the probe had already run.
    """
    monkeypatch.delenv("BOT_ID")
    monkeypatch.setattr(
        agent_lifecycle, "probe_boundary", lambda _workspace: pytest.fail("probed")
    )

    with pytest.raises(SystemExit, match="BOT_ID"):
        agent_lifecycle.main()


def test_an_unknown_harness_is_not_silently_a_no_op(
    past_setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEND_HARNESS", "gemini")

    with pytest.raises(ValueError, match="gemini"):
        agent_lifecycle.main()


@pytest.mark.parametrize(
    ("mask", "hidden"),
    [
        ("empty-dir", True),
        # A file mask is a /dev/null bind, which cannot be opened on bwrap's
        # nodev remount; /dev/null stands in for it here.
        (os.devnull, True),
        ("full-dir", False),
        ("plain-file", False),
        ("missing", False),
    ],
)
def test_the_view_probe_accepts_only_a_mask_that_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: str, hidden: bool
) -> None:
    (tmp_path / "empty-dir").mkdir()
    (tmp_path / "full-dir").mkdir()
    (tmp_path / "full-dir/.credentials").write_text("runner identity\n")
    (tmp_path / "plain-file").write_text("runner identity\n")
    monkeypatch.setenv("TEND_VIEW_MASKS", str(tmp_path / mask))

    if hidden:
        agent_lifecycle.probe_view(tmp_path)
    else:
        with pytest.raises(RuntimeError, match="did not mask"):
            agent_lifecycle.probe_view(tmp_path)


def _repository(root: Path) -> None:
    for argv in (
        ["init", "--initial-branch=main"],
        ["config", "user.email", "bot@example.invalid"],
        ["config", "user.name", "bot"],
    ):
        subprocess.run(
            ["/usr/bin/git", "-C", str(root), *argv], check=True, capture_output=True
        )
    (root / "tracked.txt").write_text("content\n")
    subprocess.run(
        ["/usr/bin/git", "-C", str(root), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(root), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def test_an_srt_mask_leaves_the_checkout_addable(tmp_path: Path) -> None:
    """`git add -A` must survive SRT's masks landing in the tree it commits from.

    SRT resolves its mandatory write protections against the checkout, so every
    protected path the repository lacks becomes a `/dev/null` bind that git
    refuses to index. Creating one needs CAP_MKNOD, which the sandbox account
    does not hold; a symlink to `/dev/null` is a character device by the same
    `stat`, so it stands in for the bind here.
    """
    _repository(tmp_path)
    (tmp_path / ".bashrc").symlink_to(os.devnull)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/commands").symlink_to(os.devnull)
    (tmp_path / "untracked.txt").write_text("a session's own new file\n")

    agent_lifecycle.exclude_sandbox_masks(tmp_path)

    untracked = subprocess.run(
        ["/usr/bin/git", "-C", str(tmp_path), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert untracked == "?? untracked.txt\n"
