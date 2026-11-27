"""Verify a candidate Codex CLI rotates subscription credentials as Tend needs.

Runs the refresh action's forced refresh against a login no production
workflow holds, then requires the access-only projection of the rotated bundle
to complete a model request. The rotated full bundle is written back to the
test environment's own secret, so the next run starts from a live token. It
never touches the ``tend`` or ``tend-codex-refresh`` environments.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
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


def rotate(*, version: str, model: str, repository: str, auth_json: str) -> None:
    """Rotate AUTH_JSON with Codex VERSION, persist it, and use its access view."""
    full = auth.parse_full(auth_json, AUTH_SECRET)
    # Prove the write-back works before Codex consumes the refresh token:
    # rewriting the unchanged login is harmless, while a rotation that cannot
    # be persisted strands the only valid token.
    auth.set_environment_secret(repository, ENVIRONMENT, AUTH_SECRET, full)

    env = clean_env()
    with tempfile.TemporaryDirectory(prefix="tend-codex-rotation-") as directory:
        root = Path(directory)
        cli = install_cli(version, root / "cli", env)
        click.echo(f"Installed Codex {version}")

        home = root / "refresh-home"
        auth_file = home / "auth.json"
        auth.stage_forced_refresh(full, auth_file)
        # The refresh action's own request, so the test covers what ships.
        try:
            exit_code: int | str = subprocess.run(
                [
                    str(cli),
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                    "--config",
                    'cli_auth_credentials_store="file"',
                    "Reply with the single word OK without using tools.",
                ],
                cwd=root,
                env={**env, "CODEX_HOME": str(home)},
                check=False,
                capture_output=True,
                timeout=180,
            ).returncode
        except subprocess.TimeoutExpired:
            # It may have rotated before hanging; persist whatever it wrote.
            exit_code = "timeout"
        refreshed = auth.read_refreshed(auth_file)
        auth.set_environment_secret(repository, ENVIRONMENT, AUTH_SECRET, refreshed)
        click.echo(f"Persisted the rotated login to {ENVIRONMENT}/{AUTH_SECRET}")
        if exit_code != 0:
            raise click.ClickException(
                f"Codex {version} refresh request failed ({exit_code}) after rotating"
            )

        old, new = full["tokens"], refreshed["tokens"]
        if new["access_token"] == old["access_token"]:
            raise click.ClickException("Codex left the access token unchanged")
        if new["account_id"] != old["account_id"]:
            raise click.ClickException("Codex refreshed into a different account")
        click.echo(
            "PASS full bundle: new access token; refresh token "
            + (
                "rotated"
                if new["refresh_token"] != old["refresh_token"]
                else "unchanged"
            )
        )

        consumer = auth.consumer_auth(refreshed)
        access_request(
            cli,
            root / "access",
            env,
            version=version,
            model=model,
            auth_json=json.dumps(consumer),
        )
    click.echo(f"PASS access-only bundle: {model} response with the rotated token")


@click.command()
@click.option("--codex-version", required=True, help="Candidate @openai/codex version.")
@click.option(
    "--model",
    default=DEFAULT_MODEL_BY_HARNESS["codex"],
    show_default=True,
    help="Model for the access-only request.",
)
def main(codex_version: str, model: str) -> None:
    """Rotate the codex-rotation-test login with a candidate Codex CLI."""
    auth_json = os.environ.get(AUTH_SECRET, "")
    if not auth_json:
        raise click.ClickException(f"{AUTH_SECRET} is not set")
    if not os.environ.get("GH_TOKEN"):
        raise click.ClickException(
            f"GH_TOKEN is empty: add {PAT_SECRET} to the {ENVIRONMENT} environment "
            "so the rotated login can be written back"
        )
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not repository:
        raise click.ClickException("GITHUB_REPOSITORY is not set")
    try:
        rotate(
            version=codex_version,
            model=model,
            repository=repository,
            auth_json=auth_json,
        )
    except (
        auth.SubscriptionAuthError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise click.ClickException(str(error)) from error
