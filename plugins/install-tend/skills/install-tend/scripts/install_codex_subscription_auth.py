#!/usr/bin/env python3
"""Provision Tend's Codex subscription credentials.

The Codex device login streams directly to the terminal. Its resulting tokens
are validated, split into refreshable and consumer bundles, and piped to GitHub
without being printed. The isolated Codex home is removed when the process
exits, including after a failure.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FULL_SECRET = "CODEX_REFRESH_AUTH_JSON"
CONSUMER_SECRET = "CODEX_AUTH_JSON"
REFRESH_PAT_SECRET = "CODEX_REFRESH_PAT"
CONSUMER_AUTH_MODE = "chatgptAuthTokens"
TEND_ENVIRONMENT = "tend"
REFRESH_ENVIRONMENT = "tend-codex-refresh"


class ProvisionError(ValueError):
    """The subscription credentials could not be provisioned safely."""


def _tokens(bundle: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    tokens = bundle.get("tokens")
    if not isinstance(tokens, dict):
        raise ProvisionError(f"{label} is missing tokens")
    return tokens


def validate_full(bundle: Mapping[str, Any]) -> None:
    """Require the refreshable ChatGPT bundle emitted by `codex login`."""
    if bundle.get("auth_mode") != "chatgpt":
        raise ProvisionError("Codex auth.json must have auth_mode 'chatgpt'")
    tokens = _tokens(bundle, "Codex auth.json")
    for field in ("access_token", "refresh_token", "id_token", "account_id"):
        value = tokens.get(field)
        if not isinstance(value, str) or not value:
            raise ProvisionError(f"Codex auth.json is missing tokens.{field}")


def consumer_auth(full_bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the non-refreshing bundle used by concurrent Tend jobs."""
    validate_full(full_bundle)
    consumer = copy.deepcopy(dict(full_bundle))
    consumer["auth_mode"] = CONSUMER_AUTH_MODE
    consumer["OPENAI_API_KEY"] = None
    consumer["tokens"]["refresh_token"] = ""
    return consumer


def _repository_parts(repository: str) -> tuple[str, str]:
    try:
        owner, name = repository.split("/", 1)
    except ValueError as exc:
        raise ProvisionError("repository must be OWNER/REPO") from exc
    if not owner or not name or "/" in name:
        raise ProvisionError("repository must be OWNER/REPO")
    return owner, name


def pat_url(repository: str) -> str:
    """Return GitHub's prefilled fine-grained-token form for this repository."""
    owner, _ = _repository_parts(repository)
    query = urllib.parse.urlencode(
        {
            "name": "Tend Codex refresh",
            "description": f"Rotates Codex subscription credentials for {repository}",
            "target_name": owner,
            "expires_in": "none",
            "environments": "write",
        }
    )
    return f"https://github.com/settings/personal-access-tokens/new?{query}"


def _codex_command() -> list[str]:
    if codex := shutil.which("codex"):
        return [codex]
    if npx := shutil.which("npx"):
        return [npx, "-y", "@openai/codex@latest"]
    raise ProvisionError("Codex is unavailable: install `codex` or `npx`")


def _set_secret(repository: str, environment: str, name: str, value: str) -> None:
    subprocess.run(
        [
            "gh",
            "secret",
            "set",
            name,
            "--repo",
            repository,
            "--env",
            environment,
        ],
        input=value,
        text=True,
        check=True,
    )


def _prepare_refresh_environment(repository: str) -> None:
    """Create the refresh secret scope, admitting only the default branch."""
    branch = subprocess.run(
        ["gh", "api", f"repos/{repository}", "--jq", ".default_branch"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if not branch:
        raise ProvisionError("GitHub did not report a default branch")
    path = f"repos/{repository}/environments/{REFRESH_ENVIRONMENT}"
    subprocess.run(
        ["gh", "api", "-X", "PUT", path, "--input", "-"],
        input=json.dumps(
            {
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    result = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"{path}/deployment-branch-policies",
            "--jq",
            ".branch_policies[]",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    policies = [json.loads(line) for line in result.stdout.splitlines() if line]
    if not any(p["type"] == "branch" and p["name"] == branch for p in policies):
        subprocess.run(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"{path}/deployment-branch-policies",
                "-f",
                f"name={branch}",
                "-f",
                "type=branch",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
    for policy in policies:
        if policy["type"] == "branch" and policy["name"] == branch:
            continue
        subprocess.run(
            [
                "gh",
                "api",
                "-X",
                "DELETE",
                f"{path}/deployment-branch-policies/{policy['id']}",
            ],
            text=True,
            capture_output=True,
            check=True,
        )


def _verify_secrets(repository: str, environment: str, required: set[str]) -> None:
    # A shell that forces color makes `gh` colorize even a piped `--json`
    # body, and the ANSI codes land inside what `json.loads` parses.
    # `CLICOLOR_FORCE=0` is the setting that defeats it: `gh` ranks a forced
    # value above `NO_COLOR`, so `NO_COLOR` alone loses.
    env = os.environ.copy()
    env.update(NO_COLOR="1", CLICOLOR_FORCE="0")
    result = subprocess.run(
        [
            "gh",
            "secret",
            "list",
            "--repo",
            repository,
            "--env",
            environment,
            "--json",
            "name",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    names = {item["name"] for item in json.loads(result.stdout)}
    missing = required - names
    if missing:
        raise ProvisionError(
            f"GitHub did not list secret: {', '.join(sorted(missing))}"
        )


def provision(repository: str) -> None:
    """Run an isolated device login and store both resulting GitHub secrets."""
    _repository_parts(repository)
    if shutil.which("gh") is None:
        raise ProvisionError("GitHub CLI `gh` is unavailable")
    _prepare_refresh_environment(repository)

    with tempfile.TemporaryDirectory(prefix="tend-codex-") as codex_home:
        env = os.environ.copy()
        env["CODEX_HOME"] = codex_home
        subprocess.run(
            [
                *_codex_command(),
                "-c",
                'cli_auth_credentials_store="file"',
                "login",
                "--device-auth",
            ],
            env=env,
            check=True,
        )

        auth_path = Path(codex_home) / "auth.json"
        try:
            full_bundle = json.loads(auth_path.read_text())
        except FileNotFoundError as exc:
            raise ProvisionError("Codex login did not create auth.json") from exc
        except json.JSONDecodeError as exc:
            raise ProvisionError("Codex auth.json is not valid JSON") from exc
        if not isinstance(full_bundle, dict):
            raise ProvisionError("Codex auth.json must contain a JSON object")

        validate_full(full_bundle)
        _set_secret(
            repository,
            REFRESH_ENVIRONMENT,
            FULL_SECRET,
            json.dumps(full_bundle, separators=(",", ":")),
        )
        _set_secret(
            repository,
            TEND_ENVIRONMENT,
            CONSUMER_SECRET,
            json.dumps(consumer_auth(full_bundle), separators=(",", ":")),
        )
        _verify_secrets(repository, REFRESH_ENVIRONMENT, {FULL_SECRET})
        _verify_secrets(repository, TEND_ENVIRONMENT, {CONSUMER_SECRET})

    print(f"Installed Codex subscription auth for {repository}.")


def store_pat(repository: str, token: str) -> None:
    """Validate and store the PAT used by the serialized refresh workflow."""
    _repository_parts(repository)
    token = token.strip()
    if not token.startswith("github_pat_"):
        raise ProvisionError("input is not a fine-grained GitHub PAT")
    if shutil.which("gh") is None:
        raise ProvisionError("GitHub CLI `gh` is unavailable")
    _prepare_refresh_environment(repository)
    _set_secret(repository, REFRESH_ENVIRONMENT, REFRESH_PAT_SECRET, token)
    _verify_secrets(repository, REFRESH_ENVIRONMENT, {FULL_SECRET, REFRESH_PAT_SECRET})
    _verify_secrets(repository, TEND_ENVIRONMENT, {CONSUMER_SECRET})
    print(f"Installed Codex refresh credential for {repository}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("provision", "pat-url", "store-pat"))
    parser.add_argument("--repo", required=True, help="GitHub repository as OWNER/REPO")
    args = parser.parse_args()

    try:
        if args.operation == "provision":
            provision(args.repo)
        elif args.operation == "pat-url":
            print(pat_url(args.repo))
        else:
            store_pat(args.repo, sys.stdin.read())
    except (ProvisionError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
