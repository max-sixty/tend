"""Contracts for the single trusted launch and post-sandbox result bottleneck.

What the unit's settings do to the agent needs systemd, a kernel and a second
uid, so `proxy/test-setup-sandbox.sh` covers it on a hosted runner.
"""

from __future__ import annotations

import ast
import base64
import os
import pwd
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import launch_agent as launch
import pytest

RUNTIME_STEP_FILES = (
    "_common.py",
    "_prompt.py",
    "_sandbox.py",
    "agent_lifecycle.py",
    "event_checkout.py",
    "run_claude.py",
    "restore-sensitive-config.sh",
    "lib/pin-instruction-paths.sh",
)


def configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, harness: str
) -> tuple[Path, Path, Path]:
    runner_temp = tmp_path / "runner-temp"
    run_dir = tmp_path / "agent-home" / "run"
    runner_temp.mkdir()
    run_dir.mkdir(parents=True)
    agent_env = runner_temp / "agent-env"
    agent_tmp = run_dir.parent / "tmp"
    agent_tmp.mkdir()
    agent_env.write_text(
        f"HOME=/home/tend-sandbox\nTMPDIR={agent_tmp}\nGITHUB_TOKEN=dummy\n"
    )
    output = runner_temp / "github-output"
    output.touch()
    summary = runner_temp / "step-summary"
    summary.touch()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    private = runtime_root / "private"
    private.mkdir(mode=0o700)
    action = tmp_path / "private/action"
    steps = action / "shared/steps"
    steps.mkdir(parents=True)
    for name in RUNTIME_STEP_FILES:
        (steps / name).parent.mkdir(parents=True, exist_ok=True)
        (steps / name).write_text(f"{name}\n")
    codex = action / "codex/runner.py"
    codex.parent.mkdir()
    codex.write_text("runner\n")
    runner_home = tmp_path / "home/runner"
    # The default self-hosted layout: the installation holds `_work` too.
    installed_runner = runner_home / "actions-runner"
    workspace = installed_runner / "_work/repo/repo"
    file_commands = installed_runner / "_work/_temp/_runner_file_commands"
    file_commands.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (installed_runner / "bin").mkdir()
    (installed_runner / "_diag").mkdir()
    (installed_runner / ".credentials").write_text("runner service identity\n")
    monkeypatch.setattr(launch, "runner_install_directory", lambda: installed_runner)
    # The sandbox account exists only on a runner; the map it feeds is tested
    # on its own below.
    monkeypatch.setattr(launch.pwd, "getpwnam", lambda _name: pwd.getpwuid(os.getuid()))
    environment = {
        "SANDBOX": "tend-sandbox",
        "TEND_RUNNER_HOME": str(runner_home),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_ENV": str(file_commands / "set_env_0"),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output),
        "TEND_RUN_DIR": str(run_dir),
        "TEND_AGENT_TMP_DIR": str(agent_tmp),
        "AGENT_ENV_FILE": str(agent_env),
        "TEND_PROXY_PORT": "8899",
        "TEND_HARNESS": harness,
        "AGENT_HOME": str(run_dir.parent),
        "GITHUB_STEP_SUMMARY": str(summary),
        "TEND_RUNTIME_ROOT": str(runtime_root),
        "TEND_PRIVATE_DIR": str(private),
        "ACTION_PATH": str(action),
    }
    if harness == "codex":
        environment["TEND_CODEX_RUNNER"] = str(codex)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    # A tend session running this suite sets it, and `launch` reads it.
    monkeypatch.delenv("TEND_AUTO_MEMORY_DIRECTORY", raising=False)
    return run_dir, output, summary


class Launch:
    """The commands the supervisor ran, and what the agent's unit was handed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.environment = b""

    @property
    def unit(self) -> list[str]:
        return next(args for args in self.calls if f"--unit={launch.UNIT}" in args)

    def setting(self, name: str) -> list[str]:
        prefix = f"--property={name}="
        return [arg.removeprefix(prefix) for arg in self.unit if arg.startswith(prefix)]


def fake_launch(
    monkeypatch: pytest.MonkeyPatch,
    run_dir: Path,
    *,
    harness: str,
    write_summary: bool = True,
    reaped: bool = True,
    failing: str | None = None,
) -> Launch:
    """Stand in for every command, running the agent's unit as a callback.

    ``failing`` names a command whose argv containing it exits non-zero.
    """
    launched = Launch()

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        launched.calls.append(args)
        if failing is not None and failing in args:
            raise subprocess.CalledProcessError(32, args)
        if args[:2] == ["/usr/bin/pgrep", "-u"]:
            return subprocess.CompletedProcess(args, 1 if reaped else 0)
        if f"--unit={launch.UNIT}" in args:
            env_file = Path(launched.setting("EnvironmentFile")[0])
            launched.environment = env_file.read_bytes()
            if harness == "claude":
                (run_dir / "tend-stream.json").write_bytes(b'{"type":"result"}\n')
                (run_dir / "tend-claude-stderr.log").write_bytes(b"diagnostic\n")
            else:
                (run_dir / "codex-final-message.md").write_bytes(b"finished\n")
            if write_summary:
                (run_dir.parent / "tmp/step-summary.md").write_bytes(b"skill result\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    if not reaped:
        # This sandbox never goes quiet: one pass, not the whole poll deadline.
        monkeypatch.setattr(launch, "REAP_DEADLINE_SEC", 0.0)
    return launched


def test_claude_exports_only_fixed_runner_owned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir, output, summary = configure(tmp_path, monkeypatch, harness="claude")
    monkeypatch.setenv("GITHUB_TOKEN", "runner-token-must-not-cross")
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "runner-service-must-not-cross")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    monkeypatch.setenv("JAVA_HOME", "/usr/lib/jvm/temurin-21")
    launched = fake_launch(monkeypatch, run_dir, harness="claude")

    assert launch.main() == 0

    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["sandbox_reaped"] == "true"
    exported = Path(values["stream_json"])
    assert exported.read_bytes() == b'{"type":"result"}\n'
    assert exported.is_relative_to(tmp_path / "runner-temp")
    assert (
        tmp_path / "runner-temp/tend-claude-stderr.log"
    ).read_bytes() == b"diagnostic\n"
    assert summary.read_bytes() == b"skill result\n\n"

    env_file = Path(launched.setting("EnvironmentFile")[0])
    assert env_file.parent == tmp_path / "runtime/private"
    # Removed once the unit has read it: it holds the job's whole environment.
    assert not env_file.exists()
    entries = launched.environment.decode().splitlines()
    assert 'GITHUB_TOKEN="dummy"' in entries
    assert 'GITHUB_ACTOR="octocat"' in entries
    # The job's own environment crosses whole, and none of it on the command
    # line `sudo` logs.
    assert 'JAVA_HOME="/usr/lib/jvm/temurin-21"' in entries
    assert not any(
        "JAVA_HOME" in arg or "octocat" in arg
        for call in launched.calls
        for arg in call
    )
    assert b"runner-token-must-not-cross" not in launched.environment
    assert b"runner-service-must-not-cross" not in launched.environment
    assert not any(entry.startswith("GITHUB_OUTPUT=") for entry in entries)
    assert f'TMPDIR="{run_dir.parent / "tmp"}"' in entries
    assert f'GITHUB_STEP_SUMMARY="{run_dir.parent / "tmp/step-summary.md"}"' in entries
    # A later entry wins, and the launch's HOME is the job's.
    runner_home = tmp_path / "home/runner"
    assert entries.index(f'HOME="{runner_home}"') > entries.index(
        'HOME="/home/tend-sandbox"'
    )
    assert f'XDG_CACHE_HOME="{runner_home / ".cache"}"' in entries

    # Nothing the agent prints between these two lines is a workflow command.
    printed = capsys.readouterr().out
    token = re.search(r"^::stop-commands::(tend-[0-9a-f]+)$", printed, re.MULTILINE)
    assert token is not None
    assert f"\n::{token[1]}::\n" in printed


def test_codex_base64_encodes_the_fixed_final_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, output, summary = configure(tmp_path, monkeypatch, harness="codex")
    fake_launch(monkeypatch, run_dir, harness="codex")

    assert launch.main() == 0

    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert base64.b64decode(values["final_message"]) == b"finished\n"
    assert values["sandbox_reaped"] == "true"
    assert summary.read_bytes() == b"skill result\n\n"


def test_runtime_bundle_is_staged_outside_the_private_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _output, _summary = configure(tmp_path, monkeypatch, harness="codex")
    launched = fake_launch(monkeypatch, run_dir, harness="codex")

    assert launch.main() == 0

    bundle = tmp_path / "runtime/action"
    assert launched.unit[-1] == str(bundle / "shared/steps/agent_lifecycle.py")
    entries = launched.environment.decode().splitlines()
    assert f'ACTION_PATH="{bundle}"' in entries
    assert f'TEND_CODEX_RUNNER="{bundle / "codex/runner.py"}"' in entries
    assert (bundle / "shared/steps/event_checkout.py").read_text() == (
        "event_checkout.py\n"
    )
    assert (bundle / "shared/steps/lib/pin-instruction-paths.sh").read_text() == (
        "lib/pin-instruction-paths.sh\n"
    )
    assert (bundle / "shared/steps").stat().st_mode & 0o777 == 0o755
    assert (bundle / "shared/steps/lib").stat().st_mode & 0o777 == 0o755


def test_the_unit_binds_the_view_over_the_home_and_hides_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _output, _summary = configure(tmp_path, monkeypatch, harness="claude")
    launched = fake_launch(monkeypatch, run_dir, harness="claude")

    assert launch.main() == 0

    home = tmp_path / "home/runner"
    view = tmp_path / "runtime/view/merged"
    assert launched.setting("BindPaths") == [f"{view}:{home}"]
    assert launched.setting("ReadWritePaths") == [f"{home} {run_dir.parent}"]
    assert set(map(Path, launched.setting("InaccessiblePaths"))) == set(
        launch.view_masks(home)
    )
    # The view and the bridge come down after the unit, and after the reap.
    commands = [" ".join(call) for call in launched.calls]
    unit_at = commands.index(" ".join(launched.unit))
    reap_at = next(i for i, c in enumerate(commands) if "/usr/bin/pkill" in c)
    assert unit_at < reap_at
    bridge = f"{launch.BRIDGE}.socket {launch.BRIDGE}.service"
    assert commands[reap_at + 2 :] == [
        f"/usr/bin/sudo /usr/bin/systemctl stop {bridge}",
        f"/usr/bin/sudo /usr/bin/umount {view}",
    ]


def test_a_failed_launch_still_reaps_and_unwinds_only_what_it_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, output, _summary = configure(tmp_path, monkeypatch, harness="claude")
    launched = fake_launch(
        monkeypatch,
        run_dir,
        harness="claude",
        failing="--socket-property=PrivateNetwork=yes",
    )

    assert launch.main() == 1

    assert output.read_text().startswith("sandbox_reaped=true\n")
    assert not any(f"--unit={launch.UNIT}" in call for call in launched.calls)
    assert not any("/usr/bin/systemctl" in call for call in launched.calls)
    assert launched.calls[-1][-2:] == [
        "/usr/bin/umount",
        str(tmp_path / "runtime/view/merged"),
    ]
    assert not (tmp_path / "runtime/private/tend-launch-env").exists()


def test_a_failed_overlay_mount_leaves_no_bind_of_the_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _output, _summary = configure(tmp_path, monkeypatch, harness="claude")
    launched = fake_launch(monkeypatch, run_dir, harness="claude", failing="overlay")

    assert launch.main() == 1

    unmounts = [call[1:] for call in launched.calls if "/usr/bin/umount" in call]
    assert unmounts == [["/usr/bin/umount", "-l", str(tmp_path / "runtime/view/lower")]]


def test_agent_step_summary_symlink_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _output, summary = configure(tmp_path, monkeypatch, harness="codex")
    secret = tmp_path / "runner-secret"
    secret.write_text("must not cross\n")
    (run_dir.parent / "tmp/step-summary.md").symlink_to(secret)
    fake_launch(monkeypatch, run_dir, harness="codex", write_summary=False)

    assert launch.main() == 0
    assert summary.read_bytes() == b""


def test_reap_waits_for_the_kill_to_take_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pkill` returns once the signals are queued, not once the UID is gone.

    A process the kernel is still tearing down — or a zombie whose parent has
    not reaped it yet — answers `pgrep` for a moment after the kill, and a
    single sample reads that as a sandbox that refused to die.
    """
    statuses = iter([0, 0, 1])
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ["/usr/bin/pgrep", "-u"]:
            return subprocess.CompletedProcess(args, next(statuses))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    assert launch.reap("tend-sandbox") is True
    assert sum(call[:2] == ["/usr/bin/pgrep", "-u"] for call in calls) == 3


def test_reap_gives_up_at_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A UID that never goes quiet fails the reap rather than hanging the job."""
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(launch, "REAP_DEADLINE_SEC", 0.0)

    assert launch.reap("tend-sandbox") is False
    assert sum(call[:2] == ["/usr/bin/pgrep", "-u"] for call in calls) == 1


def test_no_agent_owned_result_is_read_until_the_uid_is_quiescent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, output, summary = configure(tmp_path, monkeypatch, harness="claude")
    fake_launch(monkeypatch, run_dir, harness="claude", reaped=False)

    assert launch.main() == 1

    assert output.read_text() == "sandbox_reaped=false\n"
    assert summary.read_bytes() == b""
    assert not (tmp_path / "runner-temp/tend-agent-export/claude-stream.json").exists()


def test_the_environment_file_quotes_what_systemd_would_unescape() -> None:
    """systemd reads a double-quoted value literally but for these four escapes.

    Checked against systemd 255 on ubuntu-24.04: each value below reached the
    unit byte for byte.
    """
    body = launch.environment_file(
        ["PLAIN=a b=c", 'TRICKY=$HOME `x` "q" \\n\\', "LINES=one\ntwo\n", "EMPTY="]
    )

    assert body == (
        b'PLAIN="a b=c"\n'
        b'TRICKY="\\$HOME \\`x\\` \\"q\\" \\\\n\\\\"\n'
        b'LINES="one\ntwo\n"\n'
        b'EMPTY=""\n'
    )


def test_a_name_systemd_would_drop_is_left_out_aloud(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert launch.environment_file(["my-var=1", "KEPT=2"]) == b'KEPT="2"\n'
    assert "::warning::'my-var' cannot cross" in capsys.readouterr().out


def test_the_runner_mask_never_takes_the_job_s_own_tree_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default self-hosted layout keeps `_work` inside the installation."""
    configure(tmp_path, monkeypatch, harness="claude")
    runner_home = tmp_path / "home/runner"
    installed = runner_home / "actions-runner"

    masks = launch.view_masks(runner_home)

    assert installed / ".credentials" in masks
    assert installed / "_diag" in masks
    assert installed / "bin" in masks
    assert installed / "_work" not in masks
    assert installed / "_work/_temp/_runner_file_commands" in masks
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    assert not any(workspace.is_relative_to(mask) for mask in masks)


def parsed(entries: str, kind: str) -> dict[int, int]:
    """The map as `{id seen in the mount: id on disk}` for one id type."""
    seen: dict[int, int] = {}
    for entry in entries.split():
        entry_kind, mount, host, count = entry.split(":")
        assert int(count) > 0, f"{entry} is an empty range, which mount rejects"
        if entry_kind != kind:
            continue
        for offset in range(int(count)):
            assert int(mount) + offset not in seen, f"{entry} overlaps an earlier range"
            seen[int(mount) + offset] = int(host) + offset
    return seen


def account(uid: int, gid: int) -> pwd.struct_passwd:
    return pwd.struct_passwd(("name", "x", uid, gid, "", "/home/name", "/bin/sh"))


@pytest.mark.parametrize(
    ("runner", "sandbox"),
    [
        (account(1001, 1001), account(1002, 1002)),
        # The sandbox account created first, with the lower id.
        (account(1050, 1050), account(999, 999)),
        # A primary group that differs from the uid.
        (account(1001, 127), account(1002, 1002)),
        # `useradd -g`: both accounts share one group, so there is nothing to swap.
        (account(1001, 1000), account(1002, 1000)),
    ],
)
def test_the_map_swaps_the_two_accounts_and_is_identity_elsewhere(
    runner: pwd.struct_passwd, sandbox: pwd.struct_passwd
) -> None:
    entries = launch.swap_map(runner, sandbox)
    for kind, ours, theirs in (
        ("u", runner.pw_uid, sandbox.pw_uid),
        ("g", runner.pw_gid, sandbox.pw_gid),
    ):
        table = parsed(entries, kind)
        swapped = {ours: theirs, theirs: ours}
        assert table == {id_: swapped.get(id_, id_) for id_ in range(launch.ID_CEILING)}


def test_an_unmappable_account_is_refused_rather_than_truncated() -> None:
    with pytest.raises(ValueError, match="outside the mappable range"):
        launch.identity_map("u", 1001, launch.ID_CEILING)


def test_runner_cancellation_is_raised_through_the_reap_path() -> None:
    previous = signal.getsignal(signal.SIGTERM)

    with pytest.raises(launch.Cancelled) as raised, launch.raise_on_cancel():
        os.kill(os.getpid(), signal.SIGTERM)

    assert raised.value.signum == signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) is previous


def test_runtime_bundle_carries_every_module_it_imports() -> None:
    """The bundle is what the sandbox executes from; a missing import is a crash.

    Nothing in the sandbox can reach back to the action checkout, so a bundled
    module that imports a sibling left out of `RUNTIME_STEP_FILES` fails at
    `import` inside the unit — a green unit suite and a red agent turn.
    """
    steps = Path(__file__).resolve().parent
    bundled = {name for name in launch.RUNTIME_STEP_FILES if name.endswith(".py")}
    sources = {steps / name for name in bundled}
    sources.add(steps.parents[1] / "codex/runner.py")

    for source in sorted(sources):
        tree = ast.parse(source.read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        local = {f"{name}.py" for name in imported if (steps / f"{name}.py").is_file()}
        assert local <= bundled, (
            f"{source.name} imports {sorted(local - bundled)}, which "
            "RUNTIME_STEP_FILES does not stage into the sandbox bundle"
        )
