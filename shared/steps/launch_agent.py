"""Run one Tend lifecycle as the sandbox uid, in a hardened systemd unit.

The launch is two transient units, started with ``sudo systemd-run``::

    tend-proxy.socket     127.0.0.1:<proxy port>, in the agent's network namespace
      tend-proxy.service  systemd-socket-proxyd, outside it, to the credential proxy
    tend-agent.service    python3 agent_lifecycle.py, as the sandbox uid

The boundary is the agent unit's settings, :func:`unit_properties`, and systemd
enforces each of them. Tend adds the view and the reap.

The view is how the agent sees the runner's home: a copy-on-write overlay that
the unit binds over the home, so the agent reads what ``setup:`` left at the
paths it left it, and its writes never reach the runner's disk. The lower layer
is a read-only bind of that home, **idmapped** so the runner's uid/gid and the
sandbox's swap places; the upper layer sits in the runtime container.

The idmap is what makes the view writable: overlayfs checks the accessing
task's credentials against the lower inode's owner, so a ``runner:runner``
lower refuses every create by the sandbox uid. It is a swap with identity
elsewhere because overlayfs gives a copied-up inode the mapped lower owner, and
an unmapped one (a root-owned file ``setup:`` left) refuses the copy-up. Ids at
or above :data:`ID_CEILING` stay unmapped. Swapping the two accounts and
leaving every other id alone is what bounds the view: the agent writes wherever
the runner could, and no further. A directory only root can write stays
unwritable, and a root-owned file only root can read stays unreadable — mapping
uid 0 as well would hand the agent both, which is more than the account whose
home this is has.

The agent's environment crosses in an ``EnvironmentFile=`` in the private
directory, which the sandbox cannot read. On the command line it would be in
``sudo``'s log and readable from ``/proc`` by any process on the host, and it
carries the job's whole environment.

The supervisor stays outside both units: it stages the bundle, composes the
environment, reaps the sandbox uid however the run ends, and exports what the
later steps read. Root runs only system binaries, from a fixed argv.

Floors: kernel 5.19 and util-linux 2.39 (``X-mount.idmap``), systemd 247.
"""

from __future__ import annotations

import base64
import contextlib
import os
import pwd
import re
import resource
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
)
#: Staged with the step bodies because `event_checkout` runs them in the sandbox.
RUNTIME_SHELL_FILES = (
    "restore-sensitive-config.sh",
    "lib/pin-instruction-paths.sh",
)
UNIT = "tend-agent"
BRIDGE = "tend-proxy"
ID_CEILING = 65536
MOUNT = "/usr/bin/mount"
#: What systemd accepts as a name in an ``EnvironmentFile=``; it drops the rest
#: without a word.
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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


def sudo(*argv: str) -> None:
    subprocess.run(["/usr/bin/sudo", *argv], stdin=subprocess.DEVNULL, check=True)


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


def runner_install_directory() -> Path:
    """The Actions runner's installation, read off this step's ancestry."""
    pid = os.getpid()
    while pid > 1:
        with contextlib.suppress(OSError):
            executable = Path(os.readlink(f"/proc/{pid}/exe"))
            if executable.name == "Runner.Worker":
                return executable.parent.parent
        # The name field may hold spaces or ")", so count from the last ")".
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        pid = int(stat.rsplit(")", 1)[1].split()[1])
    raise ValueError("no Actions runner process among this step's ancestors")


def view_masks(home: Path) -> list[Path]:
    """What inside the job's home the agent must not read.

    The runner installation's entries (its credentials, its logs) except any a
    job path lives under, since the default self-hosted layout keeps ``_work``
    there; and the file-command directory, for ``$GITHUB_OUTPUT`` and
    ``$GITHUB_STATE`` values no step turned into an environment variable.
    """
    keep = [
        Path(required(name)).resolve() for name in ("GITHUB_WORKSPACE", "RUNNER_TEMP")
    ]
    masks = [
        entry
        for entry in sorted(runner_install_directory().iterdir())
        if not any(path.is_relative_to(entry) for path in keep)
    ]
    masks.append(Path(required("GITHUB_ENV")).parent.resolve())
    return [path for path in masks if path.is_relative_to(home)]


def stage_runtime_bundle(runtime_root: Path) -> tuple[Path, Path, Path | None]:
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

    return bundle_root, step_root / "agent_lifecycle.py", codex_runner


def identity_map(kind: str, low: int, high: int) -> list[str]:
    """Swap ``low`` and ``high``, identity elsewhere, as ``X-mount.idmap`` entries.

    Each entry is ``<kind>:<id in the mount>:<id on disk>:<count>``.
    """
    if not 0 <= low <= high < ID_CEILING:
        raise ValueError(f"{kind} ids {low} and {high} are outside the mappable range")
    if low == high:
        return [f"{kind}:0:0:{ID_CEILING}"]
    entries = [f"{kind}:{low}:{high}:1", f"{kind}:{high}:{low}:1"]
    for start, count in (
        (0, low),
        (low + 1, high - low - 1),
        (high + 1, ID_CEILING - high - 1),
    ):
        if count:
            entries.append(f"{kind}:{start}:{start}:{count}")
    return entries


def swap_map(runner: pwd.struct_passwd, sandbox: pwd.struct_passwd) -> str:
    uids = identity_map("u", *sorted((runner.pw_uid, sandbox.pw_uid)))
    gids = identity_map("g", *sorted((runner.pw_gid, sandbox.pw_gid)))
    return " ".join(uids + gids)


def mount_view(home: Path, stage: Path, sandbox: pwd.struct_passwd) -> Path:
    """Mount the view at ``stage/merged``, which the unit binds over ``home``."""
    home_stat = home.stat()
    runner = pwd.getpwuid(home_stat.st_uid)
    lower, upper, work, merged = (
        stage / name for name in ("lower", "upper", "work", "merged")
    )
    stage.mkdir(mode=0o700)
    for directory in (lower, upper, work, merged):
        directory.mkdir()
    # The merged root takes the upper directory's owner and mode, so it has to
    # match the home as the idmap presents it.
    upper.chmod(home_stat.st_mode & 0o7777)
    sudo("/usr/bin/chown", f"{sandbox.pw_uid}:{sandbox.pw_gid}", str(upper))
    idmap = swap_map(runner, sandbox)
    sudo(MOUNT, "--bind", "-o", f"ro,X-mount.idmap={idmap}", str(home), str(lower))
    # redirect_dir: renaming a directory that lives on the lower layer (cargo
    # does, in its registry) is EXDEV without it.
    options = f"lowerdir={lower},upperdir={upper},workdir={work},redirect_dir=on"
    sudo(MOUNT, "-t", "overlay", "overlay", "-o", options, str(merged))
    # The overlay holds its own reference.
    sudo("/usr/bin/umount", "-l", str(lower))
    return merged


def start_bridge(port: str) -> None:
    """Listen on the proxy's port inside the agent's network namespace.

    The socket unit binds in a namespace of its own, which the agent's unit
    joins; ``systemd-socket-proxyd`` runs outside it and forwards each
    connection to the credential proxy on the host's loopback.
    """
    sudo(
        "/usr/bin/systemd-run",
        f"--unit={BRIDGE}",
        "--quiet",
        f"--socket-property=ListenStream=127.0.0.1:{port}",
        "--socket-property=PrivateNetwork=yes",
        f"--socket-property=JoinsNamespaceOf={UNIT}.service",
        "--property=DynamicUser=yes",
        "/usr/lib/systemd/systemd-socket-proxyd",
        f"127.0.0.1:{port}",
    )


def environment_file(entries: list[str]) -> bytes:
    """``NAME=VALUE`` entries as systemd's ``EnvironmentFile=`` reads them.

    Each value is double-quoted, which keeps newlines and outer spaces, with
    the four characters systemd unescapes inside quotes escaped. A later entry
    for a name wins.
    """
    lines = []
    for entry in entries:
        name, value = entry.split("=", 1)
        if not ENVIRONMENT_NAME.fullmatch(name):
            print(
                f"::warning::{name!r} cannot cross into the agent's environment",
                flush=True,
            )
            continue
        escaped = re.sub(r'[\\"`$]', lambda match: "\\" + match[0], value)
        try:
            lines.append(f'{name}="{escaped}"\n'.encode())
        except UnicodeEncodeError:
            raise ValueError(f"{name} is not UTF-8, which systemd requires") from None
    return b"".join(lines)


def unit_properties(
    *,
    sandbox: str,
    view: Path,
    home: Path,
    workspace: Path,
    writable: list[Path],
    masks: list[Path],
    env_file: Path,
) -> list[str]:
    """The agent unit's settings, which are the whole boundary."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    return [
        f"User={sandbox}",
        f"WorkingDirectory={workspace}",
        f"EnvironmentFile={env_file}",
        # The job's own; a unit's default soft limit is 1024.
        f"LimitNOFILE={soft}:{hard}",
        # Files: the view, the sandbox's own paths and private scratch are
        # writable, the runner's own files unreadable, and the rest read-only.
        f"BindPaths={view}:{home}",
        "ProtectSystem=strict",
        "ReadWritePaths=" + " ".join(str(path) for path in (home, *writable)),
        *(f"InaccessiblePaths={mask}" for mask in masks),
        "TemporaryFileSystem=/tmp:mode=1777 /dev/shm:mode=1777",
        "PrivateDevices=yes",
        # Network: loopback, where the bridge listens, and nothing else.
        "PrivateNetwork=yes",
        f"JoinsNamespaceOf={BRIDGE}.socket",
        # Privilege: nothing to gain, and nobody else's processes to see.
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "RestrictNamespaces=yes",
        "ProtectProc=invisible",
        # The network namespace leaves every socket on the filesystem
        # reachable (the system bus, systemd-resolved's DNS), so AF_UNIX is
        # refused outright. io_uring could open one without the socket(2)
        # call this filters.
        "RestrictAddressFamilies=~AF_UNIX",
        "SystemCallFilter=~io_uring_setup io_uring_enter io_uring_register",
        "SystemCallErrorNumber=EPERM",
    ]


def launch(
    teardown: contextlib.ExitStack,
    *,
    sandbox: str,
    home: Path,
    runtime_root: Path,
    masks: list[Path],
    env_file: Path,
    lifecycle: Path,
) -> int:
    """Mount the view, start the bridge, and run the lifecycle to its exit.

    Each piece registers its own teardown on ``teardown`` once it exists.
    """
    view = mount_view(home, runtime_root / "view", pwd.getpwnam(sandbox))
    teardown.callback(sudo, "/usr/bin/umount", str(view))
    start_bridge(required("TEND_PROXY_PORT"))
    teardown.callback(
        sudo, "/usr/bin/systemctl", "stop", f"{BRIDGE}.socket", f"{BRIDGE}.service"
    )
    memory = os.environ.get("TEND_AUTO_MEMORY_DIRECTORY")
    properties = unit_properties(
        sandbox=sandbox,
        view=view,
        home=home,
        workspace=Path(required("GITHUB_WORKSPACE")),
        writable=[Path(required("AGENT_HOME")), *([Path(memory)] if memory else [])],
        masks=masks,
        env_file=env_file,
    )
    argv = [
        "/usr/bin/sudo",
        "/usr/bin/systemd-run",
        f"--unit={UNIT}",
        "--wait",
        "--pipe",
        "--collect",
        "--quiet",
        "--service-type=exec",
        *(f"--property={setting}" for setting in properties),
        "/usr/bin/python3",
        "-E",
        "-s",
        str(lifecycle),
    ]
    # Nothing the agent prints is a workflow command.
    token = f"tend-{os.urandom(16).hex()}"
    print(f"::stop-commands::{token}", flush=True)
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, check=False).returncode
    finally:
        print(f"::{token}::", flush=True)


def export_results(
    *, output: Path, runner_temp: Path, run_dir: Path, step_summary: Path
) -> None:
    """Copy the agent's fixed result files out, once its uid is quiescent."""
    with output.open("a", encoding="utf-8") as stream:
        harness = os.environ.get("TEND_HARNESS")
        if harness == "claude":
            export_dir = runner_temp / "tend-agent-export"
            export_dir.mkdir(mode=0o700)
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
    try:
        skill_summary = read_regular_nofollow(step_summary, max_bytes=MAX_STEP_SUMMARY)
    except (OSError, ValueError) as problem:
        print(f"::warning::ignored invalid skill step summary: {problem}", flush=True)
        return
    if skill_summary:
        with Path(required("GITHUB_STEP_SUMMARY")).open("ab") as summary:
            summary.write(skill_summary)
            if not skill_summary.endswith(b"\n"):
                summary.write(b"\n")
            summary.write(b"\n")


def main() -> int:
    sandbox = required("SANDBOX")
    runner_temp = Path(required("RUNNER_TEMP")).resolve(strict=True)
    runner_home = Path(required("TEND_RUNNER_HOME")).resolve(strict=True)
    output = Path(required("GITHUB_OUTPUT"))
    run_dir = Path(required("TEND_RUN_DIR"))
    step_summary = Path(required("TEND_AGENT_TMP_DIR")) / "step-summary.md"
    runtime_root = Path(required("TEND_RUNTIME_ROOT")).resolve(strict=True)
    bundle_root, lifecycle, codex_runner = stage_runtime_bundle(runtime_root)

    masks = view_masks(runner_home)
    environment = _sandbox.launch_env(required("AGENT_ENV_FILE"))
    # Appended last, so they win over the agent env file.
    overrides = {
        "ACTION_PATH": str(bundle_root),
        "TEND_VIEW_MASKS": os.pathsep.join(str(path) for path in masks),
        # Read back after the reap, so outside the view.
        "GITHUB_STEP_SUMMARY": str(step_summary),
        "TEND_RUN_DIR": str(run_dir),
        # The job's home, which the agent env file cannot name: the install
        # steps that read it run before the view exists.
        "HOME": str(runner_home),
        "XDG_CONFIG_HOME": str(runner_home / ".config"),
        "XDG_CACHE_HOME": str(runner_home / ".cache"),
        "XDG_DATA_HOME": str(runner_home / ".local/share"),
        "XDG_STATE_HOME": str(runner_home / ".local/state"),
    }
    if codex_runner is not None:
        overrides["TEND_CODEX_RUNNER"] = str(codex_runner)
    environment.extend(f"{name}={value}" for name, value in overrides.items())
    env_file = Path(required("TEND_PRIVATE_DIR")) / "tend-launch-env"
    write_trusted(env_file, environment_file(environment))

    status = 1
    # Unwinds after the reap and the export: a failed teardown fails the step
    # without costing the results.
    with contextlib.ExitStack() as teardown:
        teardown.callback(env_file.unlink)
        try:
            with raise_on_cancel():
                status = launch(
                    teardown,
                    sandbox=sandbox,
                    home=runner_home,
                    runtime_root=runtime_root,
                    masks=masks,
                    env_file=env_file,
                    lifecycle=lifecycle,
                )
        except Cancelled as cancelled:
            status = 128 + cancelled.signum
        except (OSError, subprocess.CalledProcessError) as problem:
            print(f"::error::sandbox launch: {problem}", flush=True)
        finally:
            reaped = reap(sandbox)

        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"sandbox_reaped={'true' if reaped else 'false'}\n")
        if not reaped:
            print("::error::sandbox UID still owns a live process after reap")
            return 1
        export_results(
            output=output,
            runner_temp=runner_temp,
            run_dir=run_dir,
            step_summary=step_summary,
        )
        return status


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        UnicodeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as problem:
        print(f"sandbox supervisor: {problem}", file=sys.stderr)
        raise SystemExit(1) from None
