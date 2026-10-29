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


@runtime.command("verify-generation")
def verify_generation_command() -> None:
    """Check committed workflow files against this pinned generator."""
    from tend.runtime.shared import verify_generation

    _common.run(verify_generation.main)


@runtime.command("mention-verify")
def mention_verify_command() -> None:
    """Decide whether a mention event should start an agent."""
    from tend.runtime.shared import mention_verify

    _common.run(mention_verify.main)


@runtime.command("notifications-check")
def notifications_check_command() -> None:
    """Check the notification inbox and conflicted bot PRs."""
    from tend.runtime.shared import notifications_check

    _common.run(notifications_check.main)


@runtime.command("ci-failure-report")
def ci_failure_report_command() -> None:
    """Apply a finished run's verdict to its CI failure report."""
    from tend.runtime.shared import ci_failure_report

    _common.run(ci_failure_report.main)


@runtime.command("queue-delay")
def queue_delay_command() -> None:
    """Measure the event's wait until this job started."""
    from tend.runtime.shared import workflow_steps

    _common.run(workflow_steps.queue_delay)


@runtime.command("current-head")
def current_head_command() -> None:
    """Check whether the queued PR head is still current."""
    from tend.runtime.shared import workflow_steps

    _common.run(workflow_steps.current_head)


@runtime.command("reaction")
@click.argument("action", type=click.Choice(["add", "remove"]))
def reaction_command(action: str) -> None:
    """Add or remove the bot's eyes acknowledgment."""
    from tend.runtime.shared import workflow_steps

    _common.run(lambda: workflow_steps.reaction(remove=action == "remove"))


@runtime.group("triage-batch")
def triage_batch_group() -> None:
    """Dispatch and validate selected triage issues."""


def _run_triage_batch(operation: Callable[[], int]) -> None:
    try:
        _common.run(operation)
    except ValueError as error:
        raise click.ClickException(str(error)) from error


@triage_batch_group.command("select")
def select_triage_batch_command() -> None:
    """Recheck selected issues before expanding the workflow matrix."""
    from tend.runtime.shared import triage_batch

    _run_triage_batch(triage_batch.select)


@triage_batch_group.command("dispatch")
@click.argument("issues")
def dispatch_triage_batch_command(issues: str) -> None:
    """Dispatch a JSON array of issue IDs in batches that fit GitHub's limits."""
    from tend.runtime.shared import triage_batch

    _run_triage_batch(lambda: triage_batch.dispatch(issues))


@runtime.command("security-preflight")
def security_preflight_command() -> None:
    """Verify the repository's security policy before launching an agent."""
    from tend.runtime.shared import security_preflight

    _common.run(security_preflight.main)


@runtime.command("bot-identity")
def bot_identity_command() -> None:
    """Resolve the authenticated bot for the action."""
    from tend.runtime.shared import bot_identity

    _common.run(bot_identity.main)


@runtime.command("creation-limit")
def creation_limit_command() -> None:
    """Read a proxy admission request from stdin and return its JSON decision."""
    from tend.runtime.shared import creation_limit

    _common.run(creation_limit.main)


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


@codex.group("responses-proxy")
def responses_proxy_group() -> None:
    """Manage the isolated API-key proxy."""


@responses_proxy_group.command("start")
def start_responses_proxy_command() -> None:
    """Start the proxy and publish its loopback address."""
    from tend.runtime.codex import responses_proxy

    _common.run(responses_proxy.start)


@responses_proxy_group.command("stop")
def stop_responses_proxy_command() -> None:
    """Stop the API-key proxy after the agent turn."""
    from tend.runtime.codex import responses_proxy

    _common.run(responses_proxy.stop)
