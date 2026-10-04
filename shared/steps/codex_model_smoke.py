"""Verify the candidate release's Codex default with real subscription auth.

Install the CLI pinned by the candidate action and require a successful model
response from the generator's default. A fresh CODEX_HOME and access-only auth
keep this probe separate from the saved login's rotating refresh-token chain.
Ordinary CI's credential-free surface test cannot establish this compatibility.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import click
import codex_subscription_auth
from ruamel.yaml import YAML
from tend.config import DEFAULT_MODEL_BY_HARNESS


def verify(repository: Path, auth_json: str) -> None:
    """Request one real response using the candidate CLI/model and isolated auth."""
    action = YAML(typ="safe").load((repository / "codex/action.yaml").read_text())
    version = action["inputs"]["codex_version"]["default"]
    model = DEFAULT_MODEL_BY_HARNESS["codex"]
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_AUTH_JSON"):
        env.pop(key, None)

    with tempfile.TemporaryDirectory(prefix="tend-codex-smoke-") as directory:
        root = Path(directory)
        env["CODEX_HOME"] = str(root / "home")
        auth_file = root / "home/auth.json"
        codex_subscription_auth.prepare(
            codex_auth_json=auth_json, openai_api_key="", destination=auth_file
        )
        before = auth_file.read_bytes()
        prefix = root / "cli"
        subprocess.run(
            [
                "npm",
                "install",
                "--prefix",
                str(prefix),
                "--no-audit",
                "--no-fund",
                f"@openai/codex@{version}",
            ],
            env=env,
            check=True,
            capture_output=True,
            timeout=180,
        )
        workspace = root / "workspace"
        workspace.mkdir()
        response = root / "response.txt"
        result = subprocess.run(
            [
                str(prefix / "node_modules/.bin/codex"),
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--config",
                'cli_auth_credentials_store="file"',
                "--config",
                'model_reasoning_effort="low"',
                "--model",
                model,
                "--output-last-message",
                str(response),
                "Reply with MODEL_OK. Do not use tools.",
            ],
            cwd=workspace,
            env=env,
            check=False,
            capture_output=True,
            timeout=120,
        )
        if (
            result.returncode != 0
            or not response.is_file()
            or not response.read_text().strip()
        ):
            raise click.ClickException(
                f"Codex {version} / {model} subscription request failed "
                f"(exit {result.returncode}); release smoke did not complete"
            )
        if auth_file.read_bytes() != before:
            raise click.ClickException(
                "Codex rewrote the access-only smoke credentials"
            )
    click.echo(
        f"PASS Codex {version} / {model} / subscription: model response completed"
    )


@click.command()
@click.option(
    "--auth-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read a local login; derive an access-only copy without modifying it.",
)
def main(auth_file: Path | None) -> None:
    """Test the candidate release using CODEX_AUTH_JSON or a local login file."""
    try:
        auth_json = os.environ.get("CODEX_AUTH_JSON", "")
        if auth_file is not None:
            bundle = json.loads(auth_file.read_text())
            if isinstance(bundle, dict) and bundle.get("auth_mode") == "chatgpt":
                bundle = codex_subscription_auth.consumer_auth(bundle)
            auth_json = json.dumps(bundle)
        if not auth_json:
            raise click.ClickException(
                "Supply access-only CODEX_AUTH_JSON or --auth-file"
            )
        verify(Path(__file__).resolve().parents[2], auth_json)
    except (
        json.JSONDecodeError,
        codex_subscription_auth.SubscriptionAuthError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main()
