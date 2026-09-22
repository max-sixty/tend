"""Check out the event's tree, then run one harness, inside the agent's unit."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import _common
import event_checkout


def probe_view(workspace: Path) -> None:
    """Fail unless the view is writable and every mask is unreadable."""
    probe = workspace / ".tend-view-probe"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as problem:
        raise RuntimeError(
            f"the view is not writable at {workspace}: {problem}"
        ) from None
    probe.unlink()
    for path in filter(None, os.environ["TEND_VIEW_MASKS"].split(os.pathsep)):
        if not os.path.lexists(path) or os.access(path, os.R_OK):
            raise RuntimeError(f"the view did not mask {path}")


def probe_boundary(workspace: Path) -> None:
    """Fail unless the unit's socket filter, view and network are in force."""
    try:
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        pass
    else:
        raise RuntimeError("boundary probe created an AF_UNIX socket")

    probe_view(workspace)

    probe_url = os.environ.get("TEND_BOUNDARY_PROBE_URL")
    if probe_url:
        direct = subprocess.run(
            [
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--noproxy",
                "*",
                probe_url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if direct.returncode == 0:
            raise RuntimeError("boundary probe reached host loopback directly")
        result = subprocess.run(
            [
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--proxy",
                os.environ["HTTP_PROXY"],
                "--noproxy",
                "",
                probe_url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode or result.stdout != "tend-network-ok\n":
            raise RuntimeError("boundary probe did not traverse the credential proxy")

    probe_executable = os.environ.get("TEND_BOUNDARY_PROBE_EXECUTABLE")
    if probe_executable:
        result = subprocess.run(
            [probe_executable], check=False, capture_output=True, text=True
        )
        if result.returncode or result.stdout != "tend-tool-ok\n":
            raise RuntimeError("boundary probe cannot execute the harness tool")


def configure_git(login: str, bot_id: str) -> None:
    """Commit as the bot, from the checkout and from any clone the agent makes.

    ``HOME`` is the job's, so this edits the runner's ``.gitconfig`` through
    the view: the consumer's settings stay and the runner's disk is untouched.
    """
    for name, value in (
        ("user.name", login),
        ("user.email", f"{bot_id}+{login}@users.noreply.github.com"),
    ):
        subprocess.run(["/usr/bin/git", "config", "--global", name, value], check=True)


def main() -> int:
    env = _common.require_env("GITHUB_WORKSPACE", "BOT_NAME", "BOT_ID")
    probe_boundary(Path(env["GITHUB_WORKSPACE"]))
    os.environ["TEND_INSIDE_SANDBOX"] = "1"
    configure_git(env["BOT_NAME"], env["BOT_ID"])
    event_checkout.main()

    harness = os.environ.get("TEND_HARNESS", "")
    if harness == "claude":
        import run_claude

        return run_claude.main()
    if harness == "codex":
        runner = Path(_common.require_env("TEND_CODEX_RUNNER")["TEND_CODEX_RUNNER"])
        return subprocess.run(
            ["/usr/bin/python3", "-E", "-s", str(runner), "run"], check=False
        ).returncode
    raise ValueError(f"unknown TEND_HARNESS: {harness or '<unset>'}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    # `CalledProcessError` for the git this module and `event_checkout` run,
    # `TypeError` for `event_checkout`'s topology diagnostics: a traceback here
    # is the job's only account of why the turn never started.
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as problem:
        print(f"agent lifecycle: {problem}", file=sys.stderr)
        raise SystemExit(1) from None
