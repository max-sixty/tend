"""Contracts for the single trusted post-sandbox result bottleneck."""

from __future__ import annotations

import ast
import base64
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import launch_sandbox_runtime as launch
import pytest

RUNTIME_STEP_FILES = (
    "_common.py",
    "_prompt.py",
    "_sandbox.py",
    "agent_lifecycle.py",
    "event_checkout.py",
    "run_claude.py",
    "sandbox_runtime.mjs",
    "sandbox_setup.py",
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
        "NODE_BIN": "/trusted/node",
        "TEND_HARNESS": harness,
        "AGENT_HOME": str(run_dir.parent),
        "GITHUB_STEP_SUMMARY": str(summary),
        "TEND_RUNTIME_ROOT": str(runtime_root),
        "ACTION_PATH": str(action),
        "TEND_LIFECYCLE": str(steps / "agent_lifecycle.py"),
    }
    if harness == "codex":
        environment["TEND_CODEX_RUNNER"] = str(codex)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return run_dir, output, summary


def fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    run_dir: Path,
    *,
    harness: str,
    write_summary: bool = True,
    reaped: bool = True,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ["/usr/bin/pgrep", "-u"]:
            return subprocess.CompletedProcess(args, 1 if reaped else 0)
        if "sandbox_runtime.mjs" in args[-1]:
            if harness == "claude":
                (run_dir / "tend-stream.json").write_bytes(b'{"type":"result"}\n')
                (run_dir / "tend-claude-stderr.log").write_bytes(b"diagnostic\n")
            else:
                (run_dir / "codex-final-message.md").write_bytes(b"finished\n")
            if write_summary:
                (run_dir.parent / "tmp/step-summary.md").write_bytes(b"skill result\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_claude_exports_only_fixed_runner_owned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, output, summary = configure(tmp_path, monkeypatch, harness="claude")
    monkeypatch.setenv("GITHUB_TOKEN", "runner-token-must-not-cross")
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "runner-service-must-not-cross")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    monkeypatch.setenv("JAVA_HOME", "/usr/lib/jvm/temurin-21")
    calls = fake_runtime(monkeypatch, run_dir, harness="claude")

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
    runtime = next(args for args in calls if "sandbox_runtime.mjs" in args[-1])
    assert "GITHUB_TOKEN=dummy" in runtime
    assert "GITHUB_ACTOR=octocat" in runtime
    # The job's own environment crosses whole.
    assert "JAVA_HOME=/usr/lib/jvm/temurin-21" in runtime
    assert not any("runner-token-must-not-cross" in arg for arg in runtime)
    assert not any("runner-service-must-not-cross" in arg for arg in runtime)
    assert not any(arg.startswith("GITHUB_OUTPUT=") for arg in runtime)
    assert f"TMPDIR={run_dir.parent / 'tmp'}" in runtime
    assert f"GITHUB_STEP_SUMMARY={run_dir.parent / 'tmp/step-summary.md'}" in runtime
    # `env` takes the last assignment, and the launch's HOME is the job's.
    runner_home = tmp_path / "home/runner"
    assert runtime.index(f"HOME={runner_home}") > runtime.index(
        "HOME=/home/tend-sandbox"
    )
    assert f"XDG_CACHE_HOME={runner_home / '.cache'}" in runtime


def test_codex_base64_encodes_the_fixed_final_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, output, summary = configure(tmp_path, monkeypatch, harness="codex")
    fake_runtime(monkeypatch, run_dir, harness="codex")

    assert launch.main() == 0

    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert base64.b64decode(values["final_message"]) == b"finished\n"
    assert values["sandbox_reaped"] == "true"
    assert summary.read_bytes() == b"skill result\n\n"


def test_runtime_bundle_is_staged_outside_the_private_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _output, _summary = configure(tmp_path, monkeypatch, harness="codex")
    calls = fake_runtime(monkeypatch, run_dir, harness="codex")

    assert launch.main() == 0

    runtime = next(args for args in calls if "sandbox_runtime.mjs" in args[-1])
    bundle = tmp_path / "runtime/action"
    assert runtime[-1] == str(bundle / "shared/steps/sandbox_runtime.mjs")
    assert f"ACTION_PATH={bundle}" in runtime
    assert f"TEND_LIFECYCLE={bundle / 'shared/steps/agent_lifecycle.py'}" in runtime
    assert f"TEND_CODEX_RUNNER={bundle / 'codex/runner.py'}" in runtime
    assert (bundle / "shared/steps/sandbox_setup.py").read_text() == (
        "sandbox_setup.py\n"
    )
    assert (bundle / "shared/steps/lib/pin-instruction-paths.sh").read_text() == (
        "lib/pin-instruction-paths.sh\n"
    )
    assert (bundle / "shared/steps").stat().st_mode & 0o777 == 0o755
    assert (bundle / "shared/steps/lib").stat().st_mode & 0o777 == 0o755


def test_agent_step_summary_symlink_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _output, summary = configure(tmp_path, monkeypatch, harness="codex")
    secret = tmp_path / "runner-secret"
    secret.write_text("must not cross\n")
    (run_dir.parent / "tmp/step-summary.md").symlink_to(secret)
    fake_runtime(monkeypatch, run_dir, harness="codex", write_summary=False)

    assert launch.main() == 0
    assert summary.read_bytes() == b""


def test_no_agent_owned_result_is_read_until_the_uid_is_quiescent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, output, summary = configure(tmp_path, monkeypatch, harness="claude")
    fake_runtime(monkeypatch, run_dir, harness="claude", reaped=False)

    assert launch.main() == 1

    assert output.read_text() == "sandbox_reaped=false\n"
    assert summary.read_bytes() == b""
    assert not (tmp_path / "runner-temp/tend-agent-export/claude-stream.json").exists()


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
    `import` inside SRT — a green unit suite and a red agent turn.
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


# Mirrors LAUNCH_ATTEMPTS in sandbox_runtime.mjs.
LAUNCH_ATTEMPTS = 3

# The tests below run sandbox_runtime.mjs for real, and its first statement
# refuses any other platform; the stand-in lifecycle spawns /usr/bin/bash too.
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="sandbox_runtime.mjs requires Linux"
)

FAKE_SRT = """
import fs from "node:fs";

const state = process.env.FAKE_SRT_STATE;
const failures = Number(process.env.FAKE_SRT_FAILURES);
const resetFailures = Number(process.env.FAKE_SRT_RESET_FAILURES);

function record(name) {
  const counts = JSON.parse(fs.readFileSync(state, "utf8"));
  counts[name] = (counts[name] ?? 0) + 1;
  fs.writeFileSync(state, JSON.stringify(counts));
  return counts[name];
}

export const SandboxManager = {
  async initialize() {
    if (record("initialize") <= failures) {
      throw new Error("Failed to create bridge sockets after 5 attempts");
    }
  },
  async checkDependenciesAsync() {
    return { errors: [], warnings: [] };
  },
  async wrapWithSandboxArgv() {
    record("wrap");
    return { argv: ["/usr/bin/bash", "-c", "echo lifecycle-ran"], env: {} };
  },
  async reset() {
    if (record("reset") <= resetFailures) {
      throw new Error("Cleanup failed in initializationPromise");
    }
  },
};
"""


def run_sandbox_runtime(
    tmp_path: Path, *, failures: int, reset_failures: int = 0
) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
    """Drive the real sandbox_runtime.mjs against a stand-in SandboxManager.

    `TEND_SRT_ENTRY` is the module the runtime imports, so a fake entry
    exercises the launch path itself rather than a copy of its logic.
    """
    root = tmp_path / "srt"
    workspace = root / "workspace"
    home = root / "home"
    for directory in (root, workspace, home):
        directory.mkdir()
    entry = root / "fake-srt.mjs"
    entry.write_text(FAKE_SRT)
    state = root / "state.json"
    state.write_text("{}")
    for name in ("seccomp.json", "lifecycle.py", "event.json", "agent-env"):
        (root / name).touch()

    completed = subprocess.run(
        ["node", str(Path(__file__).resolve().parent / "sandbox_runtime.mjs")],
        env={
            "PATH": os.environ["PATH"],
            "FAKE_SRT_STATE": str(state),
            "FAKE_SRT_FAILURES": str(failures),
            "FAKE_SRT_RESET_FAILURES": str(reset_failures),
            "TEND_SRT_ENTRY": str(entry),
            "TEND_SRT_SECCOMP": str(root / "seccomp.json"),
            "TEND_LIFECYCLE": str(root / "lifecycle.py"),
            "TEND_AGENT_WORKSPACE": str(workspace),
            "TEND_RUNNER_WORKSPACE": str(root / "runner-workspace"),
            "AGENT_HOME": str(home),
            "TMPDIR": str(root),
            "TEND_RUNNER_HOME": str(root / "runner-home"),
            "ACTION_PATH": str(root),
            "GITHUB_EVENT_PATH": str(root / "event.json"),
            "AGENT_ENV_FILE": str(root / "agent-env"),
            "TEND_PROXY_PORT": "8899",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, json.loads(state.read_text())


@linux_only
def test_a_transient_sandbox_launch_failure_is_retried(tmp_path: Path) -> None:
    """SRT's bridge-socket wait is a race the run should not be lost to.

    `initializeLinuxNetworkBridge` probes five times on an `i * 100` ms
    backoff, so socat has 600 ms to bind both sockets — and Tend's config,
    an external `httpProxyPort` with no `socksProxyPort`, is the branch that
    spawns two of them into that budget. Losing the launch costs the whole
    run: zero turns, no review posted, and no session log to read.
    """
    completed, counts = run_sandbox_runtime(tmp_path, failures=1)

    assert completed.returncode == 0, completed.stderr
    assert "lifecycle-ran" in completed.stdout
    assert counts["initialize"] == 2
    assert counts["wrap"] == 1


@linux_only
def test_sandbox_launch_retries_are_bounded_and_keep_the_cause(
    tmp_path: Path,
) -> None:
    """A sandbox that never comes up still fails, on SRT's own diagnosis."""
    completed, counts = run_sandbox_runtime(tmp_path, failures=LAUNCH_ATTEMPTS)

    assert completed.returncode == 1
    assert "lifecycle-ran" not in completed.stdout
    assert counts["initialize"] == LAUNCH_ATTEMPTS
    assert "wrap" not in counts
    assert (
        "tend sandbox runtime: Failed to create bridge sockets after 5 attempts"
        in completed.stderr
    )


@linux_only
def test_a_failed_cleanup_does_not_abandon_the_remaining_attempts(
    tmp_path: Path,
) -> None:
    """SRT lets its own post-error reset() reject, so ours must tolerate that.

    Surfacing the cleanup failure instead would lose both the retry and the
    launch error the retry exists to report.
    """
    completed, counts = run_sandbox_runtime(tmp_path, failures=1, reset_failures=1)

    assert completed.returncode == 0, completed.stderr
    assert "lifecycle-ran" in completed.stdout
    assert counts["initialize"] == 2
    assert "cleanup after attempt 1 failed" in completed.stderr
