"""Run the three stateful phases of Tend's Codex harness.

The shared sandbox supervisor owns the execution lifetime; Tend's proxies own
credentials. This module owns only Codex-specific mechanics that benefit from
argv construction and file handling: installing Tend's plugins, staging the
global instructions, and writing the fixed final-message file around
``codex exec``.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
from pathlib import Path

from tend.runtime.shared import _prompt, _sandbox


def _run(
    args: list[str],
    *,
    capture: bool = False,
    check: bool = True,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input,
        stdout=subprocess.PIPE if capture else None,
        text=True,
        check=check,
    )


def _required_path(name: str) -> Path:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is unset")
    return Path(value)


def _append_agent_environment(name: str, value: str) -> None:
    with _required_path("AGENT_ENV_FILE").open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def _sandbox_command(*args: str) -> list[str]:
    """Run a preparation command as the agent uid before the launch."""
    environment = _sandbox.agent_env(_required_path("AGENT_ENV_FILE"))
    return [
        "/usr/bin/sudo",
        "-u",
        os.environ.get("SANDBOX", ""),
        "/usr/bin/env",
        *environment,
        *args,
    ]


def install_plugin() -> int:
    """Install Tend's plugins and export the runner root for skill scripts."""
    sandbox = os.environ.get("SANDBOX", "")
    if not sandbox:
        raise ValueError("SANDBOX is unset")
    action_path = _required_path("ACTION_PATH").resolve()
    marketplace_source = action_path.parent
    agent_home = _required_path("AGENT_HOME").resolve()
    marketplace_root = agent_home / "tend-marketplace"
    _run(["/usr/bin/sudo", "/usr/bin/rm", "-rf", "--", str(marketplace_root)])
    _run(["/usr/bin/sudo", "/usr/bin/mkdir", "-p", str(marketplace_root)])
    _run(
        [
            "/usr/bin/sudo",
            "/usr/bin/cp",
            "-a",
            str(marketplace_source / ".agents"),
            str(marketplace_source / "plugins"),
            f"{marketplace_root}/",
        ]
    )
    _run(
        [
            "/usr/bin/sudo",
            "/usr/bin/chown",
            "-R",
            f"{sandbox}:{sandbox}",
            str(marketplace_root),
        ]
    )
    codex = str(_required_path("CODEX_BIN"))
    _run(_sandbox_command(codex, "plugin", "marketplace", "add", str(marketplace_root)))
    _run(_sandbox_command(codex, "plugin", "add", "install-tend@tend"))
    installed = _run(
        _sandbox_command(codex, "plugin", "add", "--json", "tend-ci-runner@tend"),
        capture=True,
    )
    stdout = installed.stdout or ""
    sys.stdout.write(stdout)
    try:
        root = Path(json.loads(stdout)["installedPath"]).resolve()
    except (ValueError, KeyError, TypeError):
        root = None
    if (
        root is None
        or not root.is_relative_to(agent_home)
        or _run(
            ["/usr/bin/sudo", "-u", sandbox, "/usr/bin/test", "-d", str(root)],
            check=False,
        ).returncode
        != 0
    ):
        print(
            "::error::codex plugin add --json did not report a sandbox-owned "
            "installedPath directory"
        )
        return 1
    _append_agent_environment("CLAUDE_PLUGIN_ROOT", str(root))
    return 0


def stage_agents() -> int:
    """Compose the harness-neutral prompt and Codex tail into AGENTS.md."""
    action_path = _required_path("ACTION_PATH").resolve()
    bot_name = os.environ.get("BOT_NAME", "")
    if not bot_name:
        raise ValueError("BOT_NAME is unset")
    merge = os.environ.get("TEND_MERGE", "")
    if merge not in {"restricted", "yolo"}:
        raise ValueError(f"unknown TEND_MERGE: {merge or '<unset>'}")
    shared = (action_path.parent / "shared/system-prompt.md").read_text()
    tail = (action_path / "agents-tail.md").read_text()
    render = functools.partial(
        _prompt.render, bot_name=bot_name, merge=merge, harness="codex"
    )
    body = (
        "# Tend CI instructions (Codex harness)\n\n"
        + render(shared).rstrip("\n")
        + "\n\n"
        + render(tail).rstrip("\n")
        + "\n"
    )
    memory = os.environ.get("TEND_AUTO_MEMORY_DIRECTORY", "")
    if memory:
        instructions = (
            (action_path / "memory.md")
            .read_text()
            .replace("${TEND_AUTO_MEMORY_DIRECTORY}", memory)
        )
        body += "\n" + instructions
    sandbox = os.environ.get("SANDBOX", "")
    if not sandbox:
        raise ValueError("SANDBOX is unset")
    agents = _required_path("AGENT_HOME") / ".codex/AGENTS.md"
    _run(
        ["/usr/bin/sudo", "-u", sandbox, "/usr/bin/tee", str(agents)],
        capture=True,
        input=body,
    )
    print(f"Staged AGENTS.md at {agents} ({len(body.splitlines())} lines)")
    return 0


def run_codex() -> int:
    """Run Codex and export its final message even when the process fails."""
    if os.environ.get("TEND_INSIDE_SANDBOX") != "1":
        raise RuntimeError("Codex may run only inside the sandbox lifecycle")
    codex = str(_required_path("CODEX_BIN"))
    auth_mode = os.environ.get("AUTH_MODE", "")
    auth_args: list[str]
    if auth_mode == "api-key":
        proxy_url = os.environ.get("CODEX_PROXY_URL", "")
        if not proxy_url:
            raise ValueError("CODEX_PROXY_URL is unset")
        tool_no_proxy = os.environ.get("NO_PROXY", "")
        tool_no_proxy_lower = os.environ.get("no_proxy", "")
        auth_args = [
            "--config",
            (
                "model_providers.tend-openai={ name = 'Tend OpenAI proxy', "
                f"base_url = '{proxy_url}/v1', wire_api = 'responses' }}"
            ),
            "--config",
            'model_provider="tend-openai"',
            # Only Codex's own model client must cross the credential proxy to
            # reach the runner-owned Responses proxy. Restore the loopback
            # exclusions for shell tools so sandbox-local test servers remain
            # local to the sandbox.
            "--config",
            f"shell_environment_policy.set.NO_PROXY={json.dumps(tool_no_proxy)}",
            "--config",
            f"shell_environment_policy.set.no_proxy={json.dumps(tool_no_proxy_lower)}",
        ]
    elif auth_mode == "subscription":
        auth_args = []
    else:
        raise ValueError(f"unknown AUTH_MODE: {auth_mode or '<unset>'}")
    output_file = _required_path("TEND_RUN_DIR") / "codex-final-message.md"
    _run(["/usr/bin/rm", "-f", "--", str(output_file)])
    model = os.environ.get("MODEL", "")
    args = [
        codex,
        "exec",
        *(arg for arg in os.environ.get("EXTRA_ARGS", "").splitlines() if arg),
        *(["--model", model] if model else []),
        # The agent's unit is the sole execution sandbox. A nested Codex
        # sandbox creates a second, divergent policy surface and is
        # deliberately not selected.
        "--dangerously-bypass-approvals-and-sandbox",
        "--output-last-message",
        str(output_file),
        *auth_args,
        "--config",
        'cli_auth_credentials_store="file"',
        # How long one empty `write_stdin` may wait on a running command; it
        # returns as soon as the command exits. Codex's 5-minute default turns
        # a long foreground wait — a CI poll — into a model turn every five
        # minutes, so it is lifted to GitHub's 6-hour cap on the whole job.
        "--config",
        "background_terminal_max_timeout=21600000",
    ]
    if os.environ.get("TEND_AUTO_MEMORY_DIRECTORY"):
        # Native memories exclude exec sessions from generation and forbid
        # same-run edits. Tend's notes have their own read/write instructions.
        args.extend(["--config", "features.memories=false"])
    effort = os.environ.get("EFFORT", "")
    if effort:
        args.extend(["--config", f'model_reasoning_effort="{effort}"'])
    args.append(os.environ.get("PROMPT", ""))
    launch = ["/usr/bin/env"]
    if auth_mode == "api-key":
        # The agent's NO_PROXY includes loopback, but the runner-owned
        # Responses proxy lives on host loopback, so only Codex's API client
        # bypasses those exclusions.
        launch.extend(["NO_PROXY=", "no_proxy="])
    launch.extend(args)
    insert_at = launch.index(codex)
    launch[insert_at:insert_at] = [
        f"BOT_NAME={os.environ.get('BOT_NAME', '')}",
        f"BOT_ID={os.environ.get('BOT_ID', '')}",
        f"CI={os.environ.get('CI') or 'true'}",
    ]
    result = _run(launch, check=False)
    return result.returncode
