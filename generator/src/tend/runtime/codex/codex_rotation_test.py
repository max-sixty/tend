"""Verify a candidate Codex CLI rotates subscription credentials as Tend needs.

Runs the refresh action's forced refresh against a login no production
workflow holds, then requires the access-only projection of the rotated bundle
to complete a model request. The rotated full bundle is written back to the
test environment's own secret, so the next run starts from a live token. It
never touches the ``tend`` or ``tend-codex-refresh`` environments.

Each subcommand is its own workflow step, as in the refresh action: the
write-back token reaches only ``check-write`` and ``persist``, so no process
that holds it is an ancestor of the candidate CLI, which could otherwise read
it from ``/proc/<pid>/environ``.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import click

from tend.config import DEFAULT_MODEL_BY_HARNESS
from tend.runtime.codex import codex_subscription_auth as auth
from tend.runtime.codex.codex_model_smoke import (
    access_request,
    clean_env,
    install_cli,
)

AUTH_SECRET = "CODEX_AUTH_JSON"
ENVIRONMENT = "codex-rotation-test"
PAT_SECRET = "CODEX_ROTATION_TEST_PAT"

# The refresh action's own request, so the test covers what ships;
# test_codex_rotation_test pins it to codex/refresh/action.yaml.
REFRESH_ARGS = (
    "exec",
    "--json",
    "--skip-git-repo-check",
    "--config",
    'cli_auth_credentials_store="file"',
    "Reply with the single word OK without using tools.",
)


def _cli(state: Path) -> Path:
    return state / "cli/node_modules/.bin/codex"


def _auth_file(state: Path) -> Path:
    return state / "refresh-home/auth.json"


def _login() -> dict:
    raw = os.environ.get(AUTH_SECRET, "")
    if not raw:
        raise click.ClickException(f"{AUTH_SECRET} is not set")
    return auth.parse_full(raw, AUTH_SECRET)


def _write_token() -> str:
    if not os.environ.get("GH_TOKEN"):
        raise click.ClickException(
            f"GH_TOKEN is empty: add {PAT_SECRET} to the {ENVIRONMENT} environment "
            "so the rotated login can be written back"
        )
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not repository:
        raise click.ClickException("GITHUB_REPOSITORY is not set")
    return repository


def _without_write_token() -> None:
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        raise click.ClickException(
            "a GitHub token is set: the candidate CLI must not run under a "
            "process that holds one"
        )


def _run(operation: Callable[[], None]) -> None:
    try:
        operation()
    except (
        auth.SubscriptionAuthError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise click.ClickException(str(error)) from error


state_option = click.option(
    "--state",
    required=True,
    type=click.Path(path_type=Path),
    help="Directory the steps share.",
)


@click.group()
def main() -> None:
    """Rotate the codex-rotation-test login with a candidate Codex CLI."""


@main.command("check-write")
def check_write() -> None:
    """Rewrite the unchanged login, proving write-back before Codex spends it."""
    # A rotation that cannot be persisted strands the only valid token, while
    # rewriting the unchanged login is harmless.
    repository = _write_token()
    _run(
        lambda: auth.set_environment_secret(
            repository, ENVIRONMENT, AUTH_SECRET, _login()
        )
    )
    click.echo(f"Write-back to {ENVIRONMENT}/{AUTH_SECRET} works")


@main.command("rotate")
@click.option("--codex-version", required=True, help="Candidate @openai/codex version.")
@state_option
def rotate(codex_version: str, state: Path) -> None:
    """Install the candidate and force it to refresh the staged login."""
    _without_write_token()

    def operation() -> None:
        full = _login()
        install_cli(codex_version, state / "cli", clean_env())
        click.echo(f"Installed Codex {codex_version}")
        auth.stage_forced_refresh(full, _auth_file(state))
        try:
            exit_code: int | str = subprocess.run(
                [str(_cli(state)), *REFRESH_ARGS],
                cwd=state,
                env={**clean_env(), "CODEX_HOME": str(_auth_file(state).parent)},
                check=False,
                capture_output=True,
                timeout=180,
            ).returncode
        except subprocess.TimeoutExpired:
            exit_code = "timeout"
        # `persist` runs regardless and keeps whatever Codex wrote.
        if exit_code != 0:
            raise click.ClickException(
                f"Codex {codex_version} refresh request failed ({exit_code})"
            )

    _run(operation)


@main.command("persist")
@state_option
def persist(state: Path) -> None:
    """Write the login Codex rotated back to the test environment."""
    repository = _write_token()

    def operation() -> None:
        refreshed = auth.read_refreshed(_auth_file(state))
        auth.set_environment_secret(repository, ENVIRONMENT, AUTH_SECRET, refreshed)

    _run(operation)
    click.echo(f"Persisted the rotated login to {ENVIRONMENT}/{AUTH_SECRET}")


@main.command("verify")
@click.option("--codex-version", required=True, help="Candidate @openai/codex version.")
@click.option(
    "--model",
    default=DEFAULT_MODEL_BY_HARNESS["codex"],
    show_default=True,
    help="Model for the access-only request.",
)
@state_option
def verify(codex_version: str, model: str, state: Path) -> None:
    """Check the rotated bundle, then use its access-only view."""
    _without_write_token()

    def operation() -> None:
        refreshed = auth.read_refreshed(_auth_file(state))
        old, new = _login()["tokens"], refreshed["tokens"]
        if new["access_token"] == old["access_token"]:
            raise click.ClickException("Codex left the access token unchanged")
        if new["account_id"] != old["account_id"]:
            raise click.ClickException("Codex refreshed into a different account")
        rotated = new["refresh_token"] != old["refresh_token"]
        click.echo(
            "PASS full bundle: new access token; refresh token "
            + ("rotated" if rotated else "unchanged")
        )
        access_request(
            _cli(state),
            state / "access",
            clean_env(),
            version=codex_version,
            model=model,
            auth_json=json.dumps(auth.consumer_auth(refreshed)),
        )

    _run(operation)
    click.echo(f"PASS access-only bundle: {model} response with the rotated token")
