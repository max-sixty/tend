"""Run setup and one harness inside a single Sandbox Runtime boundary."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import event_checkout
import sandbox_setup


def probe_view(workspace: Path) -> None:
    """Fail unless the copy-on-write view is the tree this process is in.

    Three properties, each the failure the view would otherwise have silently:
    the checkout is there and writable (the whole point), the runner's own
    credentials are masked out of the home it shares, and so is the directory
    GitHub's file commands write. A run that got the real home instead of a
    view would pass the first and fail the other two; one that got no home at
    all fails the first.
    """
    if not (workspace / ".git/config").is_file():
        raise RuntimeError(f"the view holds no checkout at {workspace}")
    probe = workspace / ".tend-view-probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as problem:
        raise RuntimeError(
            f"the view is not writable at {workspace}: {problem}"
        ) from None

    for masked in masked_paths():
        try:
            revealed = os.listdir(masked) if masked.is_dir() else masked.read_bytes()
        except OSError:
            continue
        if revealed:
            raise RuntimeError(f"the view did not mask {masked}")


def masked_paths() -> list[Path]:
    """What `enter_view.py` covers, as the sandbox can name it.

    A masked directory lists nothing and a masked file reads empty, so the
    check above holds for both without knowing which it was — and a mask that
    did not take is caught here from inside as well as by `enter_view`'s own
    verification outside.
    """
    return [
        Path(path)
        for path in os.environ.get("TEND_VIEW_MASKS", "").split(os.pathsep)
        if path
    ]


def probe_boundary() -> None:
    """Fail unless SRT's Linux seccomp and read boundary are effective."""
    try:
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except PermissionError:
        pass
    else:
        raise RuntimeError("SRT capability probe created an AF_UNIX socket")

    probe_view(Path(os.environ["GITHUB_WORKSPACE"]))

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
            raise RuntimeError("SRT capability probe reached host loopback directly")
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
        if result.returncode or result.stdout != "tend-srt-network-ok\n":
            raise RuntimeError("SRT capability probe did not traverse the HTTP broker")

    probe_executable = os.environ.get("TEND_BOUNDARY_PROBE_EXECUTABLE")
    if probe_executable:
        result = subprocess.run(
            [probe_executable], check=False, capture_output=True, text=True
        )
        if result.returncode or result.stdout != "tend-srt-tool-ok\n":
            raise RuntimeError("SRT capability probe cannot execute the harness tool")


def configure_git() -> None:
    """Give the agent a commit identity and a gitignore, inside the view.

    Global rather than local to the checkout because the agent also commits
    from clones it makes itself, which inherit nothing; without it every commit
    fails with ``Author identity unknown``. The address is the bot's GitHub
    noreply one, so commits attribute to the account whose token pushes them.

    Set here rather than by a runner-side step because ``HOME`` is the job's
    home, which exists as a writable tree only once the view is up. ``git
    config --global`` therefore edits the runner's own ``.gitconfig`` through
    the view: the consumer's settings are preserved, Tend's are added, and the
    file on the runner's disk is untouched. The ignore file stays in the
    sandbox's own home, which is not a tree a pull request can plant anything
    in.
    """
    ignore = Path(os.environ["AGENT_HOME"]) / ".config/git/ignore"
    ignore.parent.mkdir(parents=True, exist_ok=True)
    ignore.write_text("/.claude/settings.local.json\n", encoding="utf-8")
    login = os.environ["BOT_NAME"]
    bot_id = os.environ["BOT_ID"]
    for name, value in (
        ("core.excludesFile", str(ignore)),
        ("user.name", login),
        ("user.email", f"{bot_id}+{login}@users.noreply.github.com"),
    ):
        subprocess.run(["/usr/bin/git", "config", "--global", name, value], check=True)


def main() -> int:
    probe_boundary()
    os.environ["TEND_INSIDE_SANDBOX"] = "1"
    configure_git()
    event_checkout.main()
    setup_code = sandbox_setup.main()
    if setup_code:
        return setup_code

    harness = os.environ.get("TEND_HARNESS", "")
    if harness == "probe":
        return 0
    if harness == "claude":
        import run_claude

        return run_claude.main()
    if harness == "codex":
        runner = Path(os.environ["TEND_CODEX_RUNNER"])
        return subprocess.run(
            ["/usr/bin/python3", "-E", "-s", str(runner), "run"], check=False
        ).returncode
    raise ValueError(f"unknown TEND_HARNESS: {harness or '<unset>'}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as problem:
        print(f"agent lifecycle: {problem}", file=sys.stderr)
        raise SystemExit(1) from None
