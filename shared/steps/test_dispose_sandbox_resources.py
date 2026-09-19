from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import dispose_sandbox_resources as dispose
import pytest


def result(args: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode)


def scratch(monkeypatch: pytest.MonkeyPatch, directory: Path, uid: int) -> None:
    """Point the /tmp sweep at a directory this suite owns."""
    monkeypatch.setattr(dispose, "SANDBOX_SCRATCH", directory)
    monkeypatch.setattr(
        dispose.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=uid)
    )


def test_accepts_only_the_dedicated_workspace_shape() -> None:
    expected = Path("/var/tmp/tend-agent-workspace-abc123")
    assert dispose.workspace_container(expected / "checkout") == expected

    for path in (
        Path("/var/tmp/tend-agent-workspace-abc123"),
        Path("/var/tmp/unrelated/checkout"),
        Path("/tmp/tend-agent-workspace-abc123/checkout"),
    ):
        with pytest.raises(ValueError):
            dispose.workspace_container(path)


def test_accepts_only_the_dedicated_runtime_shape() -> None:
    expected = Path("/var/tmp/tend-runtime.aB123z")
    assert dispose.runtime_container(expected) == expected

    for path in (
        Path("/var/tmp/tend-runtime"),
        Path("/var/tmp/tend-runtime.bad/name"),
        Path("/tmp/tend-runtime.abc123"),
    ):
        with pytest.raises(ValueError):
            dispose.runtime_container(path)


def test_the_scratch_sweep_selects_on_the_owning_uid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every entry here belongs to the suite, so only the uid can be varied.

    `verify_dispose` in `proxy/test-setup-sandbox.sh` is where a runner-owned
    entry sits beside a sandbox-owned one; chowning a fixture needs the root
    that test has and this one does not.
    """
    (tmp_path / "tend-agent-scratch").mkdir()
    (tmp_path / "pytest-of-runner").mkdir()

    scratch(monkeypatch, tmp_path, os.getuid())
    assert dispose.scratch_entries("tend-sandbox") == [
        tmp_path / "pytest-of-runner",
        tmp_path / "tend-agent-scratch",
    ]

    scratch(monkeypatch, tmp_path, os.getuid() + 1)
    assert dispose.scratch_entries("tend-sandbox") == []


def test_an_entry_that_vanishes_mid_sweep_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """/tmp is shared, so the listing can name a file another process removes.

    The stand-in supplies a listing that is already stale, which is what the
    real `iterdir` hands back when a runner process deletes its own temp file
    between the listing and the stat.
    """
    (tmp_path / "tend-agent-scratch").touch()
    scratch(monkeypatch, tmp_path, os.getuid())
    monkeypatch.setattr(
        dispose,
        "SANDBOX_SCRATCH",
        SimpleNamespace(
            iterdir=lambda: [tmp_path / "vanished", tmp_path / "tend-agent-scratch"]
        ),
    )

    assert dispose.scratch_entries("tend-sandbox") == [tmp_path / "tend-agent-scratch"]


def test_disposes_only_after_the_sandbox_uid_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = Path("/var/tmp/tend-agent-workspace-abc123/checkout")
    monkeypatch.setenv("TEND_AGENT_WORKSPACE", str(workspace))
    monkeypatch.setenv("TEND_RUNTIME_ROOT", "/var/tmp/tend-runtime.r1a2b3")
    monkeypatch.setenv("SANDBOX", "tend-sandbox")
    (tmp_path / "nuget-mutex").touch()
    scratch(monkeypatch, tmp_path, os.getuid())
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return result(args, returncode=1 if args[0] == "/usr/bin/pgrep" else 0)

    monkeypatch.setattr(dispose.subprocess, "run", run)
    monkeypatch.setattr(dispose.os.path, "lexists", lambda _path: False)

    assert dispose.main() == 0
    assert calls == [
        ["/usr/bin/pgrep", "-u", "tend-sandbox"],
        [
            "/usr/bin/sudo",
            "/usr/bin/rm",
            "-rf",
            "--",
            "/var/tmp/tend-agent-workspace-abc123",
            "/var/tmp/tend-runtime.r1a2b3",
            str(tmp_path / "nuget-mutex"),
        ],
    ]


def test_refuses_to_dispose_while_a_sandbox_process_lives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TEND_AGENT_WORKSPACE", "/var/tmp/tend-agent-workspace-abc123/checkout"
    )
    monkeypatch.setenv("SANDBOX", "tend-sandbox")
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return result(args)

    monkeypatch.setattr(dispose.subprocess, "run", run)

    assert dispose.main() == 1
    assert calls == [["/usr/bin/pgrep", "-u", "tend-sandbox"]]


def test_disposes_partial_runtime_when_workspace_was_never_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEND_AGENT_WORKSPACE", raising=False)
    monkeypatch.setenv("TEND_RUNTIME_ROOT", "/var/tmp/tend-runtime.abc123")
    monkeypatch.delenv("SANDBOX", raising=False)
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return result(args)

    monkeypatch.setattr(dispose.subprocess, "run", run)
    monkeypatch.setattr(dispose.os.path, "lexists", lambda _path: False)

    assert dispose.main() == 0
    assert calls == [
        [
            "/usr/bin/sudo",
            "/usr/bin/rm",
            "-rf",
            "--",
            "/var/tmp/tend-runtime.abc123",
        ]
    ]
