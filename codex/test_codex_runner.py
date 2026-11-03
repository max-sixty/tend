"""Tests for the Codex harness's credential-isolated runner commands."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from tend.runtime.codex import runner as codex_runner


def _result(args: list[str], *, stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args, returncode, stdout, "")


def _set_sandbox_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    action = tmp_path / "repo/codex"
    action.mkdir(parents=True)
    (action.parent / ".agents").mkdir()
    (action.parent / "plugins").mkdir()
    agent_home = tmp_path / "sandbox-home"
    agent_home.mkdir()
    agent_env = tmp_path / "agent-env"
    agent_env.write_text(
        f"HOME={agent_home}\n"
        "PATH=/usr/bin\n"
        "GH_TOKEN=ghp_tendproxydummy\n"
        "GITHUB_TOKEN=ghp_tendproxydummy\n"
    )
    monkeypatch.setenv("ACTION_PATH", str(action))
    monkeypatch.setenv("AGENT_HOME", str(agent_home))
    monkeypatch.setenv("AGENT_ENV_FILE", str(agent_env))
    monkeypatch.setenv("SANDBOX", "tend-sandbox")
    monkeypatch.setenv("CODEX_BIN", "/opt/codex/bin/codex")
    # The two the runner refuses to start without, cleared rather than set: a
    # test that wants them sets them itself, and the suite run inside a tend
    # session would otherwise inherit the live lifecycle's own values.
    monkeypatch.delenv("TEND_INSIDE_SANDBOX", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("TEND_AUTO_MEMORY_DIRECTORY", raising=False)
    return action, agent_home, agent_env


def test_install_plugins_in_the_sandbox_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, agent_home, _ = _set_sandbox_env(tmp_path, monkeypatch)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object):
        calls.append((args, kwargs))
        return _result(args)

    monkeypatch.setattr(codex_runner, "_run", run)

    assert codex_runner.install_plugin() == 0
    marketplace = agent_home / "tend-marketplace"
    assert calls[0][0] == [
        "/usr/bin/sudo",
        "/usr/bin/rm",
        "-rf",
        "--",
        str(marketplace),
    ]
    codex_calls = [args for args, _ in calls if "/opt/codex/bin/codex" in args]
    assert len(codex_calls) == 3
    assert all(
        args[:5]
        == ["/usr/bin/sudo", "-u", "tend-sandbox", "/usr/bin/env", f"HOME={agent_home}"]
        for args in codex_calls
    )
    assert codex_calls[0][-5:] == [
        "/opt/codex/bin/codex",
        "plugin",
        "marketplace",
        "add",
        str(marketplace),
    ]
    assert codex_calls[1][-3:] == ["plugin", "add", "install-tend@tend"]
    assert codex_calls[2][-3:] == ["plugin", "add", "tend-ci-runner@tend"]


@pytest.mark.parametrize("memory_enabled", [False, True])
def test_stage_agents_writes_as_the_sandbox_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_enabled: bool
) -> None:
    action, agent_home, _ = _set_sandbox_env(tmp_path, monkeypatch)
    shared = action.parent / "shared"
    shared.mkdir()
    (shared / "system-prompt.md").write_text(
        "Act as ${BOT_NAME} under ${TEND_MERGE}; keep $GH_TOKEN. "
        "Read ${SKILL:run-tend}.\n"
    )
    (action / "agents-tail.md").write_text("Look up $BOT_NAME.\n")
    if memory_enabled:
        (action / "memory.md").write_text(
            (Path(__file__).parent / "memory.md").read_text()
        )
        monkeypatch.setenv(
            "TEND_AUTO_MEMORY_DIRECTORY", "/var/tmp/tend-auto-memory.test"
        )
    monkeypatch.setenv("BOT_NAME", "tend-bot")
    monkeypatch.setenv("TEND_MERGE", "restricted")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object):
        calls.append((args, kwargs))
        return _result(args)

    monkeypatch.setattr(codex_runner, "_run", run)

    assert codex_runner.stage_agents() == 0

    agents = agent_home / ".codex/AGENTS.md"
    assert calls[0][0] == [
        "/usr/bin/sudo",
        "-u",
        "tend-sandbox",
        "/usr/bin/tee",
        str(agents),
    ]
    expected = (
        "# Tend CI instructions (Codex harness)\n\n"
        "Act as tend-bot under restricted; keep $GH_TOKEN. Read $run-tend.\n\n"
        "Look up tend-bot.\n"
    )
    if memory_enabled:
        expected += "\n" + (action / "memory.md").read_text().replace(
            "${TEND_AUTO_MEMORY_DIRECTORY}", "/var/tmp/tend-auto-memory.test"
        )
    assert calls[0][1]["input"] == expected


@pytest.mark.parametrize("memory_enabled", [False, True])
@pytest.mark.parametrize("effort", ["none", 'future"level', "future-𝚡"])
def test_run_withholds_runner_credentials_and_preserves_message_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    memory_enabled: bool,
    effort: str,
) -> None:
    _, agent_home, _ = _set_sandbox_env(tmp_path, monkeypatch)
    run_dir = agent_home / "run"
    run_dir.mkdir()
    monkeypatch.setenv("GITHUB_TOKEN", "real-github-token")
    monkeypatch.setenv("GITHUB_ENV", "/runner/github-env")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")
    monkeypatch.setenv("TEND_RUN_DIR", str(run_dir))
    monkeypatch.setenv("CODEX_PROXY_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("MODEL", "gpt-test")
    monkeypatch.setenv("EFFORT", effort)
    monkeypatch.setenv(
        "EXTRA_ARGS", "--skip-git-repo-check\n--config\nproject_doc_max_bytes=8192"
    )
    monkeypatch.setenv("PROMPT", "Review this")
    monkeypatch.setenv("BOT_NAME", "tend-bot")
    monkeypatch.setenv("BOT_ID", "123")
    monkeypatch.setenv("AUTH_MODE", "api-key")
    monkeypatch.setenv("TEND_INSIDE_SANDBOX", "1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    if memory_enabled:
        monkeypatch.setenv(
            "TEND_AUTO_MEMORY_DIRECTORY", "/var/tmp/tend-auto-memory.test"
        )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object):
        calls.append((args, kwargs))
        if "/opt/codex/bin/codex" in args:
            return _result(args, returncode=7)
        return _result(args)

    monkeypatch.setattr(codex_runner, "_run", run)

    assert codex_runner.run_codex() == 7
    codex = next(args for args, _kwargs in calls if "/opt/codex/bin/codex" in args)
    assert codex[codex.index("--output-last-message") + 1] == str(
        run_dir / "codex-final-message.md"
    )
    launch, kwargs = next(
        (args, options) for args, options in calls if "/opt/codex/bin/codex" in args
    )
    assert kwargs["check"] is False
    assert "GITHUB_TOKEN=real-github-token" not in launch
    assert "GITHUB_ENV=/runner/github-env" not in launch
    assert "OPENAI_API_KEY=real-openai-key" not in launch
    assert "BOT_NAME=tend-bot" in launch
    assert "BOT_ID=123" in launch
    assert "NO_PROXY=" in launch
    assert "no_proxy=" in launch
    codex_at = launch.index("/opt/codex/bin/codex")
    effort_config = next(
        arg for arg in launch[codex_at:] if arg.startswith("model_reasoning_effort=")
    )
    assert tomllib.loads(effort_config)["model_reasoning_effort"] == effort
    assert launch[codex_at:] == [
        "/opt/codex/bin/codex",
        "exec",
        "--skip-git-repo-check",
        "--config",
        "project_doc_max_bytes=8192",
        "--model",
        "gpt-test",
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-last-message",
        str(run_dir / "codex-final-message.md"),
        "--config",
        (
            "model_providers.tend-openai={ name = 'Tend OpenAI proxy', "
            "base_url = 'http://127.0.0.1:1234/v1', wire_api = 'responses' }"
        ),
        "--config",
        'model_provider="tend-openai"',
        "--config",
        'shell_environment_policy.set.NO_PROXY="127.0.0.1,localhost"',
        "--config",
        'shell_environment_policy.set.no_proxy="localhost,127.0.0.1"',
        "--config",
        'cli_auth_credentials_store="file"',
        "--config",
        "agents.max_concurrent_threads_per_session=9223372036854775807",
        "--config",
        "background_terminal_max_timeout=21600000",
        *(["--config", "features.memories=false"] if memory_enabled else []),
        "--config",
        f"model_reasoning_effort={json.dumps(effort, ensure_ascii=False)}",
        "Review this",
    ]


@pytest.mark.parametrize("memory_enabled", [False, True])
def test_run_uses_staged_subscription_auth_without_responses_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_enabled: bool
) -> None:
    _, agent_home, _ = _set_sandbox_env(tmp_path, monkeypatch)
    run_dir = agent_home / "run"
    run_dir.mkdir()
    monkeypatch.setenv("TEND_RUN_DIR", str(run_dir))
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.setenv("PROMPT", "Review this")
    monkeypatch.setenv("AUTH_MODE", "subscription")
    monkeypatch.setenv("TEND_INSIDE_SANDBOX", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "also-configured")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    if memory_enabled:
        monkeypatch.setenv(
            "TEND_AUTO_MEMORY_DIRECTORY", "/var/tmp/tend-auto-memory.test"
        )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object):
        calls.append((args, kwargs))
        if "/usr/bin/test" in args:
            return _result(args, returncode=1)
        return _result(args)

    monkeypatch.setattr(codex_runner, "_run", run)

    assert codex_runner.run_codex() == 0
    launch, kwargs = next(
        (args, options) for args, options in calls if "/opt/codex/bin/codex" in args
    )
    assert kwargs["check"] is False
    assert all(not item.startswith("OPENAI_API_KEY=") for item in launch)
    assert "NO_PROXY=" not in launch
    assert "no_proxy=" not in launch
    codex_at = launch.index("/opt/codex/bin/codex")
    assert launch[codex_at:] == [
        "/opt/codex/bin/codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-last-message",
        str(run_dir / "codex-final-message.md"),
        "--config",
        'cli_auth_credentials_store="file"',
        "--config",
        "agents.max_concurrent_threads_per_session=9223372036854775807",
        "--config",
        "background_terminal_max_timeout=21600000",
        *(["--config", "features.memories=false"] if memory_enabled else []),
        "Review this",
    ]


def test_run_refuses_to_create_a_second_execution_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seeded because `_set_sandbox_env` clears them below: with them set the
    # runner would proceed past the refusal, so that clearing is what the
    # assertion tests — absent the seeds it is vacuous on a bare runner.
    monkeypatch.setenv("TEND_INSIDE_SANDBOX", "1")
    monkeypatch.setenv("AUTH_MODE", "api-key")
    _set_sandbox_env(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="only inside the sandbox lifecycle"):
        codex_runner.run_codex()
