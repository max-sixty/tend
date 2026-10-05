"""Which harness `agent_lifecycle` hands the turn to, after the checkout."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tend.runtime.shared import agent_lifecycle, event_checkout


@pytest.fixture(autouse=True)
def contained_sandbox_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` exports TEND_INSIDE_SANDBOX on the real environment.

    That is the point in production — the harness reads it and the
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


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_lifecycle_reaches_the_installed_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness: str,
    installed_runtime: tuple[Path, Path],
) -> None:
    """Dispatch through a fresh interpreter; replace only the model body."""
    python, package = installed_runtime
    runner = (
        package
        / "runtime"
        / harness
        / ("run_claude.py" if harness == "claude" else "runner.py")
    )
    recorded = tmp_path / "argv"
    runner.write_text(
        "import os, sys, pathlib\n"
        "def main():\n"
        f"    pathlib.Path({str(recorded)!r}).write_text(repr("
        "[os.environ['TEND_INSIDE_SANDBOX'], sys.argv[1:], str(pathlib.Path.cwd())]))\n"
        "    return 7\n"
        "run_codex = main\n"
        "if __name__ == '__main__': raise SystemExit(main())\n"
    )
    monkeypatch.setenv("TEND_HARNESS", harness)
    child = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from tend.runtime.shared import agent_lifecycle as lifecycle; "
                "lifecycle.probe_boundary = lambda workspace: None; "
                "lifecycle.configure_git = lambda login, bot_id: None; "
                "lifecycle.event_checkout.main = lambda: 0; "
                "raise SystemExit(lifecycle.main())"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 7, child.stderr
    assert recorded.read_text() == repr(
        ["1", ["runtime", "codex", "run"] if harness == "codex" else [], str(tmp_path)]
    )


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEND_HARNESS", "gemini")

    with pytest.raises(ValueError, match="gemini"):
        agent_lifecycle.main()


@pytest.mark.parametrize(
    ("mask", "hidden"),
    [
        # What `InaccessiblePaths=` leaves: the name, with mode 000.
        ("masked-dir", True),
        ("masked-file", True),
        ("full-dir", False),
        ("plain-file", False),
        ("missing", False),
    ],
)
def test_the_view_probe_accepts_only_a_mask_that_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: str, hidden: bool
) -> None:
    (tmp_path / "masked-dir").mkdir(mode=0)
    (tmp_path / "masked-file").touch(mode=0)
    (tmp_path / "full-dir").mkdir()
    (tmp_path / "full-dir/.credentials").write_text("runner identity\n")
    (tmp_path / "plain-file").write_text("runner identity\n")
    monkeypatch.setenv("TEND_VIEW_MASKS", str(tmp_path / mask))

    try:
        if hidden:
            agent_lifecycle.probe_view(tmp_path)
        else:
            with pytest.raises(RuntimeError, match="did not mask"):
                agent_lifecycle.probe_view(tmp_path)
    finally:
        (tmp_path / "masked-dir").chmod(0o700)
