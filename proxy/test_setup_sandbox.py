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


def test_configured_path_expands_the_sandbox_home() -> None:
    """`~` is the sandbox account's home, the one directory that is not the
    job's. A runner-home entry needs no `sandbox_path:` at all now, so there is
    nothing left here to refuse."""
    assert setup_sandbox.configured_paths("~\n~/.local/bin\n/opt/tools") == [
        str(setup_sandbox.AGENT_HOME),
        str(setup_sandbox.AGENT_HOME / ".local/bin"),
        "/opt/tools",
    ]


def test_agent_path_carries_the_job_path_entry_for_entry() -> None:
    """Every directory the job's own steps resolve tools from stays on PATH.

    That is what makes a toolchain `setup:` installed — rustup's shims in
    `~/.cargo/bin`, a runner image's node — resolve for the agent exactly as it
    does for the consumer's own CI, with no entry named anywhere in config.
    """
    job_path = "/home/runner/.cargo/bin:/usr/local/bin:/usr/bin:/bin"

    entries = setup_sandbox.agent_path(
        runner_tool_path=job_path, extras=["/opt/consumer/bin"]
    )

    assert entries[0] == "/opt/consumer/bin"
    assert entries[1] == str(setup_sandbox.AGENT_HOME / ".local/bin")
    assert "/home/runner/.cargo/bin" in entries
    # Last, so a version the consumer installed stays selected.
    assert entries[-1] == str(setup_sandbox.TEND_AGENT_UV_DIR)


def test_agent_path_never_repeats_an_entry() -> None:
    entries = setup_sandbox.agent_path(
        runner_tool_path="/usr/bin:/usr/bin:/bin", extras=["/usr/bin"]
    )

    assert entries.count("/usr/bin") == 1


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("PATH=/tmp/bin", "reserved key 'PATH'"),
        ("TMPDIR=/somewhere", "reserved key 'TMPDIR'"),
        ("CLAUDE_CONFIG_DIR=/elsewhere", "reserved key 'CLAUDE_CONFIG_DIR'"),
        ("NOT_AN_ASSIGNMENT", "not NAME=VALUE"),
    ],
)
def test_consumer_environment_rejects_unsafe_records(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        setup_sandbox.consumer_env(raw)


def test_consumer_environment_refuses_to_redescribe_the_run() -> None:
    """`sandbox_env:` is applied over the job environment, so it wins — which
    makes it the one place a run could be made to lie to itself about which
    event, repository or workflow it is."""
    with pytest.raises(ValueError, match="describes the run"):
        setup_sandbox.consumer_env("GITHUB_WORKFLOW=something-else")


def test_consumer_environment_preserves_values_after_the_first_equals() -> None:
    assert setup_sandbox.consumer_env("TEND_VALUE=a=b\n") == ["TEND_VALUE=a=b"]


def test_every_fixed_agent_assignment_is_reserved() -> None:
    assignments = setup_sandbox.base_agent_env(
        "/usr/bin", ("ANTHROPIC_API_KEY", "dummy")
    )
    assert {line.split("=", 1)[0] for line in assignments} <= (
        setup_sandbox.RESERVED_SANDBOX_ENV
    )
    assert f"TMPDIR={setup_sandbox.AGENT_TMP_DIR}" in assignments


def test_agent_state_stays_out_of_the_view() -> None:
    """`HOME` follows the job's home at the launch, so anything Tend has to read
    back after the reap is addressed by its own variable instead: a write into
    the view reaches nothing but an upper layer that dies with the run."""
    assignments = setup_sandbox.base_agent_env("/usr/bin", None)

    assert f"HOME={setup_sandbox.AGENT_HOME}" in assignments
    assert f"CLAUDE_CONFIG_DIR={setup_sandbox.CLAUDE_CONFIG_DIR}" in assignments
    assert f"CODEX_HOME={setup_sandbox.CODEX_HOME}" in assignments
    assert setup_sandbox.CLAUDE_CONFIG_DIR.is_relative_to(setup_sandbox.AGENT_HOME)
    assert setup_sandbox.CODEX_HOME.is_relative_to(setup_sandbox.AGENT_HOME)
    assert setup_sandbox.TEND_RUN_DIR.is_relative_to(setup_sandbox.AGENT_HOME)


def test_github_only_agent_environment_has_no_model_credential() -> None:
    assignments = setup_sandbox.base_agent_env("/usr/bin", None)

    assert not any(
        line.startswith(("ANTHROPIC_API_KEY=", "CLAUDE_CODE_OAUTH_TOKEN="))
        for line in assignments
    )
    assert "OPENAI_API_KEY" in setup_sandbox.RESERVED_SANDBOX_ENV
    assert "CODEX_API_KEY" in setup_sandbox.RESERVED_SANDBOX_ENV
    assert "CODEX_AUTH_JSON" in setup_sandbox.RESERVED_SANDBOX_ENV
    assert "CODEX_HOME" in setup_sandbox.RESERVED_SANDBOX_ENV
    # The job's own, so the agent's checkout is the one the workflow made.
    assert not any(line.startswith("GITHUB_WORKSPACE=") for line in assignments)
    assert not any(line.startswith(("NO_PROXY=", "no_proxy=")) for line in assignments)


def test_tend_secrets_never_live_where_the_agent_can_read_them(
    tmp_path: Path,
) -> None:
    """The proxy's confdir holds its CA private key, and the agent reads the
    runner's home with the runner account's own permissions. So these live in
    the runtime container, outside the overlaid tree, where the sandbox uid is
    plain "other"."""
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


def test_runner_home_does_not_trust_an_empty_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "")

    assert (
        setup_sandbox.runner_home() == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    )
