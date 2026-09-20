"""Trusted outer supervisor for one SRT-contained Tend lifecycle.

The launch chain this builds is::

    sudo unshare --mount --propagation private
      python3 enter_view.py …            # root: the copy-on-write view
        setpriv --reuid <sandbox>        # exec, so one process tree throughout
          env -i <agent env>
            node sandbox_runtime.mjs     # SRT, then bwrap, then the lifecycle

:mod:`enter_view` is what puts the agent in the job's own home and checkout;
everything from ``setpriv`` down is the boundary Tend already had. The
supervisor stays outside all of it: it stages the bundle, composes the
environment, reaps the sandbox uid however the run ends, and exports what the
later steps read.
"""

from __future__ import annotations

import base64
import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import _sandbox
from _safe_files import read_regular_nofollow

MAX_FIXED_EXPORT = 64 * 1024 * 1024
MAX_FINAL_MESSAGE = 256 * 1024
MAX_STEP_SUMMARY = 512 * 1024
MAX_RUNTIME_FILE = 2 * 1024 * 1024
RUNTIME_STEP_FILES = (
    "_common.py",
    "_prompt.py",
    "_sandbox.py",
    "agent_lifecycle.py",
    "event_checkout.py",
    "run_claude.py",
    "sandbox_runtime.mjs",
    "sandbox_setup.py",
)
#: Staged with the step bodies because `event_checkout` runs them inside SRT.
RUNTIME_SHELL_FILES = (
    "restore-sensitive-config.sh",
    "lib/pin-instruction-paths.sh",
)
#: The Actions runner's own executables. The one that is an ancestor of this
#: process names the directory holding the runner's service credentials, which
#: is the one thing inside the job's home the agent must not read.
RUNNER_EXECUTABLES = frozenset({"Runner.Worker", "Runner.Listener"})


class Cancelled(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum


@contextlib.contextmanager
def raise_on_cancel() -> Iterator[None]:
    """Turn runner cancellation into control flow that reaches the UID reap."""
    previous: dict[signal.Signals, signal.Handlers] = {}

    def cancel(signum: int, _frame: FrameType | None) -> None:
        raise Cancelled(signum)

    for watched in (signal.SIGINT, signal.SIGTERM):
        previous[watched] = signal.signal(watched, cancel)
    try:
        yield
    finally:
        for watched, handler in previous.items():
            signal.signal(watched, handler)


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is unset")
    return value


def reap(sandbox: str) -> bool:
    subprocess.run(
        ["/usr/bin/sudo", "/usr/bin/pkill", "-KILL", "-u", sandbox],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return (
        subprocess.run(
            ["/usr/bin/pgrep", "-u", sandbox],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 1
    )


def write_trusted(path: Path, body: bytes, *, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(body)
        while view:
            view = view[os.write(descriptor, view) :]
    finally:
        os.close(descriptor)


def mkdir_traversable(path: Path) -> None:
    path.mkdir(mode=0o755)
    path.chmod(0o755)


def parent_pid(pid: int) -> int:
    """Read one process's parent from procfs.

    The comm field is the process name in parentheses and may itself contain
    spaces or a closing parenthesis, so the fields are counted from the last
    one rather than by splitting the whole line.
    """
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    return int(stat.rsplit(")", 1)[1].split()[1])


def runner_install_directory() -> Path:
    """Locate the Actions runner's installation from this process's ancestry.

    Derived rather than configured, so it follows the runner wherever GitHub or
    a self-hosted operator puts it, and so the set of paths the view masks
    cannot grow into a list Tend maintains. Every step body runs as a
    descendant of ``Runner.Worker``; not finding one means this is not a job
    Tend understands, which fails the run rather than guessing.
    """
    pid = os.getpid()
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            executable = Path(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            executable = None
        if executable is not None and executable.name in RUNNER_EXECUTABLES:
            if executable.parent.name != "bin":
                raise ValueError(
                    f"the Actions runner at {executable} is not in a bin/ "
                    "directory, so its installation cannot be located"
                )
            return executable.parent.parent
        pid = parent_pid(pid)
    raise ValueError(
        "no Actions runner process among this step's ancestors; Tend cannot "
        "identify the directory holding the runner's own credentials"
    )


def view_masks(home: Path) -> list[Path]:
    """The directories inside the job's home the agent must not read.

    Both come from the job rather than from a list. Anything outside the home
    needs no mask: only inside the view does the agent read with the runner
    account's own permissions.
    """
    candidates = [
        runner_install_directory(),
        # Every `$GITHUB_ENV` and `$GITHUB_OUTPUT` line any earlier step wrote,
        # which is where an action output or a `setup:` export lands.
        Path(required("GITHUB_ENV")).parent,
    ]
    return [path for path in candidates if path == home or path.is_relative_to(home)]


def stage_runtime_bundle(runtime_root: Path) -> tuple[Path, Path, Path, Path | None]:
    """Copy the trusted lifecycle behind a sandbox-traversable path."""
    source_root = Path(required("ACTION_PATH")).resolve(strict=True)
    bundle_root = runtime_root / "action"
    shared_root = bundle_root / "shared"
    step_root = shared_root / "steps"
    for directory in (bundle_root, shared_root, step_root):
        mkdir_traversable(directory)
    for name in RUNTIME_STEP_FILES + RUNTIME_SHELL_FILES:
        body = read_regular_nofollow(
            source_root / "shared/steps" / name, max_bytes=MAX_RUNTIME_FILE
        )
        if body is None:
            raise ValueError(f"runtime bundle source is missing: shared/steps/{name}")
        destination = step_root / name
        if destination.parent != step_root:
            mkdir_traversable(destination.parent)
        write_trusted(destination, body, mode=0o644)

    codex_runner: Path | None = None
    if os.environ.get("TEND_CODEX_RUNNER"):
        body = read_regular_nofollow(
            source_root / "codex/runner.py", max_bytes=MAX_RUNTIME_FILE
        )
        if body is None:
            raise ValueError("runtime bundle source is missing: codex/runner.py")
        codex_runner = bundle_root / "codex/runner.py"
        mkdir_traversable(codex_runner.parent)
        write_trusted(codex_runner, body, mode=0o644)

    return source_root, bundle_root, step_root / "agent_lifecycle.py", codex_runner


def main() -> int:
    sandbox = required("SANDBOX")
    runner_temp = Path(required("RUNNER_TEMP")).resolve(strict=True)
    runner_home = Path(required("TEND_RUNNER_HOME")).resolve(strict=True)
    real_output = Path(required("GITHUB_OUTPUT"))
    export_dir = runner_temp / "tend-agent-export"
    export_dir.mkdir(mode=0o700)
    run_dir = Path(required("TEND_RUN_DIR"))
    step_summary_dir = Path(required("TEND_AGENT_TMP_DIR"))
    runtime_root = Path(required("TEND_RUNTIME_ROOT")).resolve(strict=True)
    source_root, bundle_root, lifecycle, codex_runner = stage_runtime_bundle(
        runtime_root
    )

    masks = view_masks(runner_home)
    environment = _sandbox.launch_env(required("AGENT_ENV_FILE"))
    # Values the sandbox reads that name something this step computed, so they
    # are appended last and win.
    overrides = {
        "ACTION_PATH": str(bundle_root),
        "TEND_LIFECYCLE": str(lifecycle),
        # So the lifecycle's own probe can check the masks took effect rather
        # than re-deriving where they should be.
        "TEND_VIEW_MASKS": os.pathsep.join(str(path) for path in masks),
        # The step summary and the run directory are read back after the reap,
        # so both have to sit outside the view — a write into the view reaches
        # nothing but its own upper layer.
        "GITHUB_STEP_SUMMARY": str(step_summary_dir / "step-summary.md"),
        "TEND_RUN_DIR": str(run_dir),
    }
    if codex_runner is not None:
        overrides["TEND_CODEX_RUNNER"] = str(codex_runner)
    environment.extend(f"{name}={value}" for name, value in overrides.items())

    argv = [
        "/usr/bin/sudo",
        "/usr/bin/unshare",
        "--mount",
        "--propagation",
        "private",
        "--",
        "/usr/bin/python3",
        "-E",
        "-s",
        str(source_root / "shared/steps/enter_view.py"),
        "--home",
        str(runner_home),
        # Inside the per-run runtime container, which the dispose step already
        # removes, and mode 0700 under its 0755 parent so the upper layer is
        # invisible to the sandbox that writes into it.
        "--stage",
        str(runtime_root / "view"),
        "--user",
        sandbox,
        *(argument for path in masks for argument in ("--mask", str(path))),
        # Everything past this point is what `enter_view` execs once the view
        # is up, behind the `setpriv` it composes from the account it resolved.
        "--",
        "/usr/bin/env",
        "-i",
        *environment,
        required("NODE_BIN"),
        str(bundle_root / "shared/steps/sandbox_runtime.mjs"),
    ]

    status = 1
    reaped = False
    try:
        with raise_on_cancel():
            status = subprocess.run(
                argv, stdin=subprocess.DEVNULL, check=False
            ).returncode
    except Cancelled as cancelled:
        status = 128 + cancelled.signum
    finally:
        reaped = reap(sandbox)

    with real_output.open("a", encoding="utf-8") as stream:
        stream.write(f"sandbox_reaped={'true' if reaped else 'false'}\n")
        if reaped:
            harness = os.environ.get("TEND_HARNESS")
            if harness == "claude":
                exported_stream = export_dir / "claude-stream.json"
                body = read_regular_nofollow(
                    run_dir / "tend-stream.json", max_bytes=MAX_FIXED_EXPORT
                )
                write_trusted(exported_stream, body or b"")
                stderr = read_regular_nofollow(
                    run_dir / "tend-claude-stderr.log", max_bytes=MAX_FIXED_EXPORT
                )
                write_trusted(runner_temp / "tend-claude-stderr.log", stderr or b"")
                stream.write(f"stream_json={exported_stream}\n")
            elif harness == "codex":
                message = read_regular_nofollow(
                    run_dir / "codex-final-message.md", max_bytes=MAX_FINAL_MESSAGE
                )
                if message is not None:
                    stream.write(
                        f"final_message={base64.b64encode(message).decode('ascii')}\n"
                    )
    if reaped:
        try:
            skill_summary = read_regular_nofollow(
                step_summary_dir / "step-summary.md", max_bytes=MAX_STEP_SUMMARY
            )
        except (OSError, ValueError) as problem:
            print(
                f"::warning::ignored invalid skill step summary: {problem}", flush=True
            )
        else:
            if skill_summary:
                with Path(required("GITHUB_STEP_SUMMARY")).open("ab") as summary:
                    summary.write(skill_summary)
                    if not skill_summary.endswith(b"\n"):
                        summary.write(b"\n")
                    summary.write(b"\n")
    if not reaped:
        print("::error::sandbox UID still owns a live process after reap", flush=True)
        return 1
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError) as problem:
        print(f"sandbox supervisor: {problem}", file=sys.stderr)
        raise SystemExit(1) from None
