"""Tend's action runtime CLI.

Commands call the same installed package used by the generator. The action
supplies configuration through its environment; Click owns command selection,
arguments and usage errors. Domain functions retain their operation results
and GitHub file outputs, including best-effort memory warnings. Domain imports
are deferred until the command runs so Linux-only action dependencies do not
prevent the portable generator or CLI help from loading.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import click

from tend.runtime.codex import codex_model_smoke, codex_subscription_auth
from tend.runtime.shared import _common


@click.group()
def runtime() -> None:
    """Run Tend's harness and action operations."""


@runtime.command("security-preflight")
def security_preflight_command() -> None:
    """Verify the repository's security policy before launching an agent."""
    from tend.runtime.shared import security_preflight

    _common.run(security_preflight.main)


@runtime.command("rate-limit-preflight")
def rate_limit_preflight_command() -> None:
    """Check whether account limits allow an agent run."""
    from tend.runtime.shared import rate_limit_preflight

    _common.run(rate_limit_preflight.main)


@runtime.command("launch-agent")
def launch_agent_command() -> None:
    """Launch an agent in the protected systemd unit."""
    from tend.runtime.shared import launch_agent

    _common.run(launch_agent.main)


@runtime.command("agent-lifecycle")
def agent_lifecycle_command() -> None:
    """Check the sandbox boundary, check out the event, and run its harness."""
    from tend.runtime.shared import agent_lifecycle

    try:
        result = agent_lifecycle.main()
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        raise click.ClickException(f"agent lifecycle: {error}") from error
    click.get_current_context().exit(result)


@runtime.command("dispose-sandbox-resources")
def dispose_sandbox_resources_command() -> None:
    """Reap the agent and release its sandbox resources."""
    from tend.runtime.shared import dispose_sandbox_resources

    _common.run(dispose_sandbox_resources.main)


@runtime.command("mark-notification-read")
def mark_notification_read_command() -> None:
    """Mark a notification read after recording its outcome."""
    from tend.runtime.shared import mark_notification_read

    _common.run(mark_notification_read.main)


@runtime.command("report-failure")
def report_failure_command() -> None:
    """Publish an unsuccessful run's diagnostic summary."""
    from tend.runtime.shared import report_failure

    _common.run(report_failure.main)


@runtime.command("token-usage")
@click.option("--harness", type=click.Choice(("claude", "codex")), required=True)
def token_usage_command(harness: str) -> None:
    """Consolidate session logs and publish the run's token usage."""
    from tend.runtime.shared import token_usage

    _common.run(lambda: token_usage.main(harness))


@runtime.command("copy-session-tree")
@click.argument("source", type=click.Path(path_type=Path))
@click.argument("destination", type=click.Path(path_type=Path))
@click.argument("uid", type=click.IntRange(min=0))
@click.argument("gid", type=click.IntRange(min=0))
def copy_session_tree_command(
    source: Path, destination: Path, uid: int, gid: int
) -> None:
    """Copy a bounded session tree as root, assigning it to UID and GID."""
    from tend.runtime.shared import token_usage

    if os.geteuid() != 0:
        raise click.ClickException("copy-session-tree requires root")
    _common.run(
        lambda: token_usage.privileged_copy(source, destination, uid=uid, gid=gid)
    )


@runtime.group()
def claude() -> None:
    """Prepare and run the Claude harness."""


@claude.command("compose-system-prompt")
def compose_system_prompt_command() -> None:
    """Compose the shared system prompt and the repository's instructions."""
    from tend.runtime.claude import compose_system_prompt

    _common.run(compose_system_prompt.main)


@claude.command("run")
def run_claude_command() -> None:
    """Run Claude and classify the session's result."""
    from tend.runtime.claude import run_claude

    _common.run(run_claude.main)


@runtime.group()
def codex() -> None:
    """Prepare, verify and run the Codex harness."""


codex.add_command(codex_model_smoke.main, "model-smoke")


@codex.command("check-cli")
def check_codex_cli_command() -> None:
    """Verify the pinned Codex CLI's credential-free compatibility contract."""
    from tend.runtime.codex import codex_surface

    click.get_current_context().exit(codex_surface.main())


def _run_codex(operation: Callable[[], int]) -> None:
    try:
        result = operation()
    except (OSError, RuntimeError, ValueError) as error:
        raise click.ClickException(f"codex runner: {error}") from error
    except subprocess.CalledProcessError as error:
        click.get_current_context().exit(error.returncode or 1)
    click.get_current_context().exit(result)


@codex.command("install-plugin")
def install_plugin_command() -> None:
    """Install Tend's Codex plugin into the action's staging home."""
    from tend.runtime.codex import runner as codex_runner

    _run_codex(codex_runner.install_plugin)


@codex.command("stage-agents")
def stage_agents_command() -> None:
    """Stage Codex's agent instructions and skill entry points."""
    from tend.runtime.codex import runner as codex_runner

    _run_codex(codex_runner.stage_agents)


@codex.command("run")
def run_codex_command() -> None:
    """Run Codex inside the agent sandbox."""
    from tend.runtime.codex import runner as codex_runner

    _run_codex(codex_runner.run_codex)


@codex.group()
def auth() -> None:
    """Prepare consumer credentials or rotate refresh-owner credentials."""


def _run_auth(operation: Callable[[], int]) -> None:
    try:
        result = operation()
    except codex_subscription_auth.SubscriptionAuthError as error:
        raise click.ClickException(str(error)) from error
    click.get_current_context().exit(result)


@auth.command("prepare")
@click.argument("destination", type=click.Path(path_type=Path))
def prepare_auth_command(destination: Path) -> None:
    """Select consumer auth from CODEX_AUTH_JSON or OPENAI_API_KEY."""
    _run_auth(lambda: codex_subscription_auth.prepare_command(destination))


@auth.command("stage-refresh")
@click.argument("destination", type=click.Path(path_type=Path))
def stage_refresh_auth_command(destination: Path) -> None:
    """Stage the refresh owner's credentials at DESTINATION."""
    _run_auth(lambda: codex_subscription_auth.stage_refresh_command(destination))


@auth.command("publish-refresh")
@click.argument("auth_file", type=click.Path(path_type=Path))
def publish_refresh_auth_command(auth_file: Path) -> None:
    """Publish the rotated AUTH_FILE after a successful refresh."""
    _run_auth(lambda: codex_subscription_auth.publish_refresh_command(auth_file))


@runtime.group("gist-memory")
def memory() -> None:
    """Synchronize the opt-in secret Gist memory."""


@memory.command("restore")
def restore_memory_command() -> None:
    """Restore memory before launching an agent."""
    from tend.runtime.shared import gist_memory

    click.get_current_context().exit(gist_memory.restore_command())


@memory.command("save")
def save_memory_command() -> None:
    """Save the agent's memory changes after its run."""
    from tend.runtime.shared import gist_memory

    click.get_current_context().exit(gist_memory.save_command())


@memory.command("cleanup")
def cleanup_memory_command() -> None:
    """Remove the disposable local memory copy."""
    from tend.runtime.shared import gist_memory

    click.get_current_context().exit(gist_memory.cleanup_command())
