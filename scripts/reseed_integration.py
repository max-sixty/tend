"""Reconcile the integration fixture's environment and reseed its credentials.

Only main may receive the real bot and model secrets. All deployment-policy
pages are reconciled, and repository-level copies that bypass the gate are
removed. Missing source secrets fail before any write.
"""

from __future__ import annotations

import json
import os
import subprocess

REPOSITORY = "tend-agent/tend-integration"
ENVIRONMENT = f"repos/{REPOSITORY}/environments/tend"
SECRETS = {
    "TEND_BOT_TOKEN": "GH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN": "CLAUDE_CODE_OAUTH_TOKEN",
}


def gh(*args: str, input: str | None = None) -> str:
    return subprocess.check_output(["gh", *args], input=input, text=True)


def main() -> None:
    values = {name: os.environ.get(source, "") for name, source in SECRETS.items()}
    for name, value in values.items():
        if not value:
            raise SystemExit(f"{name} is empty")
    gh(
        "api",
        "-X",
        "PUT",
        ENVIRONMENT,
        "--input",
        "-",
        input=json.dumps(
            {
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            }
        ),
    )
    endpoint = f"{ENVIRONMENT}/deployment-branch-policies"
    pages = json.loads(gh("api", "--paginate", "--slurp", endpoint))
    policies = [policy for page in pages for policy in page["branch_policies"]]
    main_ids = {
        policy["id"]
        for policy in policies
        if policy["type"] == "branch" and policy["name"] == "main"
    }
    for policy in policies:
        if policy["id"] not in main_ids:
            gh("api", "-X", "DELETE", f"{endpoint}/{policy['id']}")
    if not main_ids:
        gh("api", "-X", "POST", endpoint, "-f", "name=main", "-f", "type=branch")
    for name, value in values.items():
        gh("secret", "set", name, "--repo", REPOSITORY, "--env", "tend", input=value)
    for secret in json.loads(
        gh("secret", "list", "--repo", REPOSITORY, "--json", "name")
    ):
        if secret["name"] in SECRETS:
            gh("secret", "delete", secret["name"], "--repo", REPOSITORY)


if __name__ == "__main__":
    main()
