"""Unit coverage for sandbox setup policy that does not need a second UID."""

from __future__ import annotations

import os
import pwd
import re
import subprocess
from pathlib import Path

import pytest
import setup_sandbox


def _paths(tmp_path: Path) -> setup_sandbox.Paths:
    runner_home = tmp_path / "runner"
    runner_workspace = runner_home / "work/repo"
    runner_workspace.mkdir(parents=True)
    runner_temp = tmp_path / "temp"
    runner_temp.mkdir()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    workspace = runner_temp / "agent-repo"
    workspace.mkdir()
    action = tmp_path / "action"
    action.mkdir()
    uv = tmp_path / "uv"
    uv.mkdir()
    return setup_sandbox.Paths(
        workspace=workspace.resolve(),
        runner_workspace=runner_workspace.resolve(),
        runner_temp=runner_temp.resolve(),
        runtime_root=runtime_root.resolve(),
        action_path=action.resolve(),
        tend_uv_dir=uv.resolve(),
        github_env=tmp_path / "github-env",
        runner_home=runner_home.resolve(),
    )


def test_configured_path_accepts_the_checkout_and_refuses_the_runner_home(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    assert setup_sandbox.configured_paths(
        str(paths.workspace / "bin"), paths=paths
    ) == [str(paths.workspace / "bin")]
    with pytest.raises(ValueError, match="under the runner's home"):
        setup_sandbox.configured_paths(str(paths.runner_home / "bin"), paths=paths)


def test_workspace_path_keeps_precedence_over_runner_home_rewrite(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    workspace_bin = paths.workspace / "bin"
    workspace_bin.mkdir()

    plan = setup_sandbox.plan_agent_path(
        runner_tool_path=str(workspace_bin),
        extras=[],
        paths=paths,
        can_execute=lambda _: False,
    )

    assert str(workspace_bin) in plan.agent_path
    assert plan.dropped_home_paths == []


def test_agent_uv_fallback_trails_consumer_paths_and_needs_no_blocker(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    runner_bin = paths.runner_home / "bin"
    runner_bin.mkdir()
    for name in ("uv", "uvx", "tend-probe"):
        executable = runner_bin / name
        executable.touch(mode=0o755)
    consumer_bin = tmp_path / "consumer-bin"

    plan = setup_sandbox.plan_agent_path(
        runner_tool_path=str(runner_bin),
        extras=[str(consumer_bin)],
        paths=paths,
        can_execute=lambda _: False,
    )

    assert plan.agent_path[0] == str(consumer_bin)
    assert plan.agent_path[-1] == str(setup_sandbox.TEND_AGENT_UV_DIR)
    assert plan.blocked_commands == ["tend-probe"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("PATH=/tmp/bin", "reserved key 'PATH'"),
        ("TMPDIR=/somewhere", "reserved key 'TMPDIR'"),
        ("NOT_AN_ASSIGNMENT", "not NAME=VALUE"),
    ],
)
def test_consumer_environment_rejects_unsafe_records(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        setup_sandbox.consumer_env(raw)


def test_consumer_environment_preserves_values_after_the_first_equals() -> None:
    assert setup_sandbox.consumer_env("TEND_VALUE=a=b\n") == ["TEND_VALUE=a=b"]


def test_every_fixed_agent_assignment_is_reserved() -> None:
    assignments = setup_sandbox.base_agent_env(
        "/usr/bin", ("ANTHROPIC_API_KEY", "dummy"), workspace=Path("/agent")
    )
    assert {line.split("=", 1)[0] for line in assignments} <= (
        setup_sandbox.RESERVED_SANDBOX_ENV
    )
    assert f"TMPDIR={setup_sandbox.AGENT_TMP_DIR}" in assignments


def test_github_only_agent_environment_has_no_model_credential() -> None:
    assignments = setup_sandbox.base_agent_env(
        "/usr/bin", None, workspace=Path("/agent")
    )

    assert not any(
        line.startswith(("ANTHROPIC_API_KEY=", "CLAUDE_CODE_OAUTH_TOKEN="))
        for line in assignments
    )
    assert "OPENAI_API_KEY" in setup_sandbox.RESERVED_SANDBOX_ENV
    assert "CODEX_API_KEY" in setup_sandbox.RESERVED_SANDBOX_ENV
    assert "CODEX_AUTH_JSON" in setup_sandbox.RESERVED_SANDBOX_ENV
    assert "CODEX_HOME" in setup_sandbox.RESERVED_SANDBOX_ENV
    assert "GITHUB_WORKSPACE=/agent" in assignments
    assert not any(line.startswith(("NO_PROXY=", "no_proxy=")) for line in assignments)


def test_workspace_handoff_never_dereferences_pr_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    calls: list[tuple[str, ...]] = []

    def sudo(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(setup_sandbox, "sudo", sudo)

    assert setup_sandbox.handoff_workspace(paths)
    assert calls[0] == (
        "/usr/bin/chown",
        "--recursive",
        "--no-dereference",
        f"{setup_sandbox.SANDBOX}:{setup_sandbox.SANDBOX}",
        str(paths.workspace),
    )


def test_import_derives_one_destination_per_form(tmp_path: Path) -> None:
    """A runner path lands under the sandbox home by basename; a
    checkout-relative one keeps its relative position in the clone."""
    paths = _paths(tmp_path)
    registry = paths.runner_home / ".cargo/registry"
    registry.mkdir(parents=True)
    (paths.runner_workspace / "target").mkdir()

    plan = setup_sandbox.plan_imports(f"{registry}\ntarget", paths=paths)

    assert plan.copies == [
        (registry, setup_sandbox.AGENT_HOME / "imports/registry"),
        (paths.runner_workspace / "target", paths.workspace / "target"),
    ]


def test_import_copies_as_the_sandbox_user_and_leaves_the_host_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy bypasses the ancestor chain and nothing else.

    `/home/runner` is 0750 and the sandbox user is in none of the runner's
    groups, so it cannot traverse to any import; the bind in a private mount
    namespace is what makes the source reachable. Everything inside is still
    read with the sandbox uid's own credentials, which is why `setpriv` drops
    to it before `cp` rather than copying as root.

    The source is still on the runner afterwards, untouched and unmoved —
    the invariant the whole mechanism exists to keep, and what retires the
    chown, the cleanup step, and the self-hosted gate.
    """
    paths = _paths(tmp_path)
    registry = paths.runner_home / ".cargo/registry"
    registry.mkdir(parents=True)
    (registry / "warm").write_text("cache\n")
    account = pwd.getpwuid(os.getuid())
    monkeypatch.setattr(setup_sandbox.pwd, "getpwnam", lambda _name: account)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def sudo(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(setup_sandbox, "sudo", sudo)
    setup_sandbox.apply_imports(
        setup_sandbox.plan_imports(str(registry), paths=paths), paths=paths
    )

    destination = setup_sandbox.AGENT_HOME / "imports/registry"
    stage = paths.workspace.parent / "import-stage"
    (make_dir, make_kwargs), (copy, copy_kwargs) = calls
    assert make_dir == ("/usr/bin/mkdir", "-p", str(destination))
    assert make_kwargs["user"] == setup_sandbox.SANDBOX
    assert copy == (
        "/usr/bin/env",
        f"TEND_IMPORT_SOURCE={registry}",
        f"TEND_IMPORT_STAGE={stage}",
        f"TEND_IMPORT_DESTINATION={destination}",
        f"TEND_IMPORT_UID={account.pw_uid}",
        f"TEND_IMPORT_GID={account.pw_gid}",
        "/usr/bin/unshare",
        "--mount",
        "--propagation",
        "private",
        "--",
        "/bin/sh",
        "-c",
        setup_sandbox.IMPORT_COPY,
    )
    # Root, because only root can create the namespace and the bind; the drop
    # to the sandbox uid happens inside, in the script.
    assert "user" not in copy_kwargs
    assert "--reuid" in setup_sandbox.IMPORT_COPY
    assert "--clear-groups" in setup_sandbox.IMPORT_COPY
    # Consumer paths reach `sh` as environment, never as script text.
    assert str(registry) not in setup_sandbox.IMPORT_COPY
    assert (registry / "warm").read_text() == "cache\n"
    assert stage.stat().st_mode & 0o777 == 0o755


def test_import_fails_by_name_when_a_file_inside_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 0600 credential is refused by the kernel, not by a list here — and
    the run stops rather than continuing with an import missing a file."""
    paths = _paths(tmp_path)
    secrets = paths.runner_home / "private"
    secrets.mkdir(parents=True)
    monkeypatch.setattr(
        setup_sandbox.pwd, "getpwnam", lambda _name: pwd.getpwuid(os.getuid())
    )

    def sudo(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "/usr/bin/unshare" in args:
            return subprocess.CompletedProcess(
                args, 1, "", "cp: cannot open 'id_rsa': Permission denied"
            )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(setup_sandbox, "sudo", sudo)
    plan = setup_sandbox.plan_imports(str(secrets), paths=paths)

    with pytest.raises(ValueError, match=re.escape(str(secrets))) as raised:
        setup_sandbox.apply_imports(plan, paths=paths)
    assert "Permission denied" in str(raised.value)


def test_import_refuses_two_entries_that_would_collide(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    for parent in ("a", "b"):
        (paths.runner_home / parent / "cache").mkdir(parents=True)
    entries = f"{paths.runner_home / 'a/cache'}\n{paths.runner_home / 'b/cache'}"

    with pytest.raises(ValueError, match="both copy to") as raised:
        setup_sandbox.plan_imports(entries, paths=paths)
    assert "a/cache" in str(raised.value)
    assert "b/cache" in str(raised.value)


def test_import_refuses_a_missing_directory_rather_than_skipping_it(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    for entry in (str(tmp_path / "never-created"), "never-created"):
        with pytest.raises(ValueError, match="not a directory on the runner"):
            setup_sandbox.plan_imports(entry, paths=paths)


def test_import_refuses_the_runner_checkout_git_directory(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    (paths.runner_workspace / ".git/modules").mkdir(parents=True)

    with pytest.raises(ValueError, match=re.escape("runner checkout's `.git`")):
        setup_sandbox.plan_imports(".git/modules", paths=paths)


@pytest.mark.parametrize("entry", ["target", "target/release"])
def test_a_copy_never_follows_a_symlink_the_pull_request_planted(
    tmp_path: Path, entry: str
) -> None:
    """The destination sits in the event tree, which on a review is the pull
    request's own, so a symlink at any component of it would put a
    runner-prepared directory wherever the pull request pointed."""
    paths = _paths(tmp_path)
    (paths.runner_workspace / entry).mkdir(parents=True)
    elsewhere = tmp_path / "planted"
    elsewhere.mkdir()
    (paths.workspace / "target").symlink_to(elsewhere)

    with pytest.raises(ValueError, match="resolves outside the agent's checkout"):
        setup_sandbox.plan_imports(entry, paths=paths)


def test_import_refuses_a_destination_the_event_tree_already_carries(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    (paths.runner_workspace / "docs").mkdir()
    (paths.workspace / "docs").mkdir()

    with pytest.raises(ValueError, match="already exists in the agent's checkout"):
        setup_sandbox.plan_imports("docs", paths=paths)


@pytest.mark.parametrize("entry", ["~", "~user/cache"])
def test_import_refuses_a_tilde_that_names_no_runner_directory(
    tmp_path: Path, entry: str
) -> None:
    with pytest.raises(ValueError, match="runner home|the runner's own home"):
        setup_sandbox.plan_imports(entry, paths=_paths(tmp_path))


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


def test_global_git_config_carries_the_bot_commit_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def sudo(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(setup_sandbox, "sudo", sudo)

    setup_sandbox.configure_global_git(login="tend-agent", bot_id="42")

    settings = {args[-2]: args[-1] for args in calls if "config" in args}
    assert settings["user.name"] == "tend-agent"
    assert settings["user.email"] == "42+tend-agent@users.noreply.github.com"
    assert settings["core.excludesFile"].endswith("/.config/git/ignore")
    assert all("--global" in args for args in calls if "config" in args)
