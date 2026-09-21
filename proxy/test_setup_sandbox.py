"""Unit coverage for sandbox setup policy that does not need a second UID."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest
import setup_sandbox


def _paths(tmp_path: Path) -> setup_sandbox.Paths:
    runner_home = tmp_path / "runner"
    workspace = runner_home / "work/repo/repo"
    workspace.mkdir(parents=True)
    runner_temp = runner_home / "work/_temp"
    runner_temp.mkdir(parents=True)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    private_dir = runtime_root / "private"
    private_dir.mkdir(mode=0o700)
    action = tmp_path / "action"
    action.mkdir()
    uv = tmp_path / "uv"
    uv.mkdir()
    return setup_sandbox.Paths(
        workspace=workspace.resolve(),
        runner_temp=runner_temp.resolve(),
        runtime_root=runtime_root.resolve(),
        private_dir=private_dir.resolve(),
        action_path=action.resolve(),
        tend_uv_dir=uv.resolve(),
        github_env=tmp_path / "github-env",
        runner_home=runner_home.resolve(),
    )


def test_agent_path_carries_the_job_path_entry_for_entry() -> None:
    job_path = "/home/runner/.cargo/bin:/usr/local/bin:/usr/bin:/bin"

    entries = setup_sandbox.agent_path(job_path)

    # Where the Claude binary installs.
    assert entries[0] == str(setup_sandbox.AGENT_HOME / ".local/bin")
    assert "/home/runner/.cargo/bin" in entries
    # Last, so a version the consumer installed stays selected.
    assert entries[-1] == str(setup_sandbox.TEND_AGENT_UV_DIR)


def test_agent_path_never_repeats_an_entry() -> None:
    entries = setup_sandbox.agent_path("/usr/bin:/usr/bin:/bin")

    assert entries.count("/usr/bin") == 1


def test_agent_state_stays_out_of_the_view() -> None:
    """Tend reads these back after the reap, so they cannot follow `HOME`."""
    assignments = setup_sandbox.base_agent_env("/usr/bin", None)

    assert f"HOME={setup_sandbox.AGENT_HOME}" in assignments
    assert f"CLAUDE_CONFIG_DIR={setup_sandbox.CLAUDE_CONFIG_DIR}" in assignments
    assert f"CODEX_HOME={setup_sandbox.CODEX_HOME}" in assignments
    assert f"TMPDIR={setup_sandbox.AGENT_TMP_DIR}" in assignments
    assert setup_sandbox.CLAUDE_CONFIG_DIR.is_relative_to(setup_sandbox.AGENT_HOME)
    assert setup_sandbox.CODEX_HOME.is_relative_to(setup_sandbox.AGENT_HOME)
    assert setup_sandbox.TEND_RUN_DIR.is_relative_to(setup_sandbox.AGENT_HOME)


def test_github_only_agent_environment_has_no_model_credential() -> None:
    assignments = setup_sandbox.base_agent_env("/usr/bin", None)

    assert not any(
        line.startswith(("ANTHROPIC_API_KEY=", "CLAUDE_CODE_OAUTH_TOKEN="))
        for line in assignments
    )
    # The job's own, so the agent's checkout is the one the workflow made.
    assert not any(line.startswith("GITHUB_WORKSPACE=") for line in assignments)
    assert "NO_PROXY=localhost,127.0.0.1,::1" in assignments


def test_tend_secrets_never_live_where_the_agent_can_read_them(
    tmp_path: Path,
) -> None:
    """The proxy's confdir holds its CA private key; the agent reads the
    runner's home as the runner."""
    paths = _paths(tmp_path)

    for path in (paths.confdir, paths.proxy_log, paths.proxy_pid):
        assert path.is_relative_to(paths.private_dir)
        assert not path.is_relative_to(paths.runner_home)
    assert paths.private_dir.stat().st_mode & 0o777 == 0o700


def test_proxy_uvx_isolated_from_consumer_python_and_uv_configuration(
    tmp_path: Path,
) -> None:
    command = setup_sandbox.uvx_command(
        _paths(tmp_path), version="1.2.3", args=["--version"]
    )

    assert command[1:6] == [
        "--no-config",
        "--no-python-downloads",
        "--python",
        "/usr/bin/python3",
        "--from",
    ]
    assert command[-3:] == ["mitmproxy==1.2.3", "mitmdump", "--version"]


def test_a_checkout_outside_the_runner_home_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """The view covers the home; a self-hosted work folder elsewhere is not."""
    inside = _paths(tmp_path)
    setup_sandbox.require_checkout_in_view(inside)

    elsewhere = tmp_path / "opt/actions-runner/_work/repo/repo"
    elsewhere.mkdir(parents=True)
    outside = setup_sandbox.Paths(**{**vars(inside), "workspace": elsewhere.resolve()})
    with pytest.raises(ValueError) as refused:
        setup_sandbox.require_checkout_in_view(outside)
    assert str(elsewhere.resolve()) in str(refused.value)
    assert str(inside.runner_home) in str(refused.value)
    assert "self-hosted" in str(refused.value)


def test_runner_home_does_not_trust_an_empty_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "")

    assert (
        setup_sandbox.runner_home() == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    )
