"""Refuse to run unless the default branch matches Tend's merge policy.

Shared verbatim by both harness actions (``claude/``, ``codex/``), where the
step is ``id: security`` and a non-zero exit is what the "Report failure" step
reads off ``steps.security.outcome``.

The step runs with the bot's own token, so ``current_user_can_bypass`` is
GitHub's direct answer. Restricted mode requires a non-bypassable update rule.
Yolo requires the exact middle state: the bot may bypass an update rule only
through a pull request, and a separate rule requires fresh CODEOWNER approval
that the bot cannot bypass. Required reviews alone do not restrict a
write-access bot from approving another author's pull request.

Decisions this encodes:

- A ruleset whose ``current_user_can_bypass`` cannot be read proves nothing
  either way. Restricted mode may fall through to the branch-protected floor
  only when GitHub's ruleset read is inconclusive; yolo fails closed because
  it needs the exact pull-request-only state.
- Any readable value other than ``never`` counts as bypassable, JSON ``null``
  included — an answer that isn't "never" is not a restriction.
- A rules listing that cannot be read, or that comes back as anything but an
  array of pages, falls through to the ``.protected`` floor too, so a GitHub
  outage doesn't take the gate down with it. A listing that reads cleanly is
  an answer, even when no entry in it is a usable update rule.

Inputs (env): ``GITHUB_REPOSITORY``, ``TEND_MERGE``, plus the bot's
``GITHUB_TOKEN``, which reaches ``gh`` through the environment.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from typing import Any
from urllib.parse import quote

import _common

BYPASS_ERROR = (
    "The bot can bypass every restrict-updates ruleset on '{branch}' "
    "(current_user_can_bypass != never), so the merge restriction does not "
    "restrict the bot. Remove the bot — or any team, role, or user exemption "
    "covering it — from the rulesets' bypass actors. See docs/security-model.md "
    "in the Tend repo."
)

NO_RULESET_ERROR = (
    "No restrict-updates ruleset covers '{branch}', so nothing confirms the bot "
    "can't merge PRs into it. Branch protection that only requires reviews "
    "doesn't count: the bot holds write, so its own approval counts on a PR "
    "someone else opened. Run `tend check --fix` as a repo admin to create the "
    "ruleset. See docs/security-model.md in the Tend repo."
)

UNPROTECTED_ERROR = (
    "Default branch '{branch}' is NOT protected. Without branch protection, "
    "the bot can merge PRs without review. Run `tend check --fix` as a repo "
    "admin to create a restrict-updates ruleset. See docs/security-model.md in "
    "the Tend repo."
)

YOLO_BYPASS_ERROR = (
    "Yolo merge mode requires the bot's effective update bypass on '{branch}' to "
    "be pull_requests_only; GitHub reported {actual}. Run `tend check --fix`."
)
YOLO_LIFECYCLE_ERROR = (
    "Yolo merge mode requires creation and deletion of '{branch}' to remain "
    "blocked; GitHub reported {actual}. Run `tend check --fix`."
)

CONTROL_PLANE_ERROR = (
    "Yolo merge mode requires a pull-request rule on '{branch}' with fresh "
    "CODEOWNER approval that this bot cannot bypass. Run `tend check --fix` "
    "after the control-plane CODEOWNERS block is merged."
)
# The block `tend check --fix` writes (tend.workflows.codeowners_config). The action
# cannot import the generator, so the tests feed its output through this copy.
CODEOWNERS_BEGIN = "# BEGIN tend control plane"
CODEOWNERS_END = "# END tend control plane"
CONTROL_PLANE_PATHS = (
    "/.github/**",
    "/.config/tend.yaml",
    "/CODEOWNERS",
    "/docs/CODEOWNERS",
    "**/CLAUDE.md",
    "**/CLAUDE.local.md",
    "**/AGENTS.md",
    "**/AGENTS.override.md",
    "**/.claude",
    "**/.claude/**",
    "**/.agents",
    "**/.agents/**",
)


def ruleset_ids(rules: Any, rule_type: str) -> list[int]:
    """The rulesets contributing ``rule_type`` to the branch, deduped."""
    if not isinstance(rules, list):
        return []
    return sorted(
        {
            rule["ruleset_id"]
            for rule in rules
            if isinstance(rule, dict)
            and rule.get("type") == rule_type
            and isinstance(rule.get("ruleset_id"), int)
        }
    )


def effective_update_bypass(rulesets: list[dict[str, Any] | None]) -> str | None:
    """Combine applying update rulesets from most to least restrictive."""
    bypasses = [
        ruleset.get("current_user_can_bypass")
        for ruleset in rulesets
        if isinstance(ruleset, dict)
    ]
    if "never" in bypasses:
        return "never"
    if any(ruleset is None for ruleset in rulesets):
        return None
    if "pull_requests_only" in bypasses:
        return "pull_requests_only"
    return "always"


def has_control_plane_review(rulesets: list[dict[str, Any] | None]) -> bool:
    """Whether one applying ruleset enforces the yolo control-plane review."""
    for ruleset in rulesets:
        if not isinstance(ruleset, dict):
            continue
        if ruleset.get("current_user_can_bypass") != "never":
            continue
        for rule in ruleset.get("rules", []):
            parameters = rule.get("parameters", {})
            if (
                rule.get("type") == "pull_request"
                and parameters.get("require_code_owner_review") is True
                and parameters.get("dismiss_stale_reviews_on_push") is True
            ):
                return True
    return False


def has_valid_control_plane_codeowners(repo: str, branch: str, bot_name: str) -> bool:
    """Whether GitHub accepts Tend's final managed CODEOWNERS block."""
    content = None
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        try:
            response = _common.gh_json(
                "api", f"repos/{repo}/contents/{path}?ref={quote(branch, safe='')}"
            )
        except _common.GH_READ_FAILED as error:
            if isinstance(error, subprocess.CalledProcessError) and "HTTP 404" in (
                error.stderr or ""
            ):
                continue
            return False
        if not isinstance(response, dict) or not isinstance(
            response.get("content"), str
        ):
            return False
        try:
            content = base64.b64decode(response["content"]).decode()
        except (ValueError, UnicodeDecodeError):
            return False
        break
    if content is None:
        return False

    lines = content.rstrip().splitlines()
    if lines.count(CODEOWNERS_BEGIN) != 1 or lines.count(CODEOWNERS_END) != 1:
        return False
    block_lines = lines[lines.index(CODEOWNERS_BEGIN) :]
    if (
        len(block_lines) != len(CONTROL_PLANE_PATHS) + 2
        or block_lines[-1] != CODEOWNERS_END
    ):
        return False
    for path, line in zip(CONTROL_PLANE_PATHS, block_lines[1:-1], strict=True):
        parts = line.split()
        if len(parts) < 2 or parts[0] != path:
            return False
        if any(
            not re.fullmatch(r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", owner)
            or owner.casefold() == f"@{bot_name}".casefold()
            for owner in parts[1:]
        ):
            return False

    # The Contents API follows symlinks and reports type=file for their targets.
    # Verify the Git mode so ownership cannot live outside protected paths.
    repo_owner, repo_name = repo.split("/", 1)
    directory = path.rpartition("/")[0]
    query = (
        "{ repository(owner: "
        + json.dumps(repo_owner)
        + ", name: "
        + json.dumps(repo_name)
        + ") { object(expression: "
        + json.dumps(f"{branch}:{directory}")
        + ") { ... on Tree { entries { name mode } } } } }"
    )
    try:
        tree = _common.gh_json("api", "graphql", "-f", f"query={query}")
        entries = tree["data"]["repository"]["object"]["entries"]
        if not any(
            entry["name"] == "CODEOWNERS" and entry["mode"] in {0o100644, 0o100755}
            for entry in entries
        ):
            return False
    except (*_common.GH_READ_FAILED, KeyError, TypeError, ValueError):
        return False

    try:
        response = _common.gh_json(
            "api", f"repos/{repo}/codeowners/errors?ref={quote(branch, safe='')}"
        )
    except _common.GH_READ_FAILED:
        return False
    if not isinstance(response, dict) or not isinstance(response.get("errors"), list):
        return False
    begin_line = content.splitlines().index(CODEOWNERS_BEGIN) + 1
    managed_lines = range(begin_line, begin_line + len(block_lines))
    return not any(
        not isinstance(error, dict)
        or error.get("line") is None
        or error.get("line") in managed_lines
        for error in response["errors"]
    )


def main() -> int:
    env = _common.require_env("GITHUB_REPOSITORY", "TEND_MERGE")
    repo = env["GITHUB_REPOSITORY"]
    merge = env["TEND_MERGE"]
    if merge not in {"restricted", "yolo"}:
        return _common.fail(f"Unknown Tend merge mode: {merge}")

    # The two reads the gate cannot proceed without are left to raise. A red
    # gate is the safe direction, and gh's own explanation — "Bad credentials",
    # "Not Found" — is already on stderr from `_common.gh`.
    default_branch = _common.gh_json("api", f"repos/{repo}")["default_branch"]

    # A GitHub blip can answer this with an HTML page under a 200, so the parse
    # fails rather than the call: catching only the non-zero exit would abort
    # the gate on an outage, and "Report failure" keys on this step's outcome,
    # so the outage would go unrecorded as well.
    try:
        rules = _common.gh_paginated(f"repos/{repo}/rules/branches/{default_branch}")
    except _common.GH_READ_FAILED:
        if merge == "yolo":
            return _common.fail(
                YOLO_BYPASS_ERROR.format(branch=default_branch, actual="unknown")
            )
        rules = None
    update_ids = ruleset_ids(rules or [], "update")
    if rules is not None and not update_ids:
        return _common.fail(NO_RULESET_ERROR.format(branch=default_branch))

    details: dict[int, dict[str, Any] | None] = {}

    def fetch(ruleset_id: int) -> dict[str, Any] | None:
        if ruleset_id in details:
            return details[ruleset_id]
        try:
            ruleset = _common.gh_json("api", f"repos/{repo}/rulesets/{ruleset_id}")
        except _common.GH_READ_FAILED:
            ruleset = None
        details[ruleset_id] = (
            ruleset
            if isinstance(ruleset, dict) and "current_user_can_bypass" in ruleset
            else None
        )
        return details[ruleset_id]

    update_rulesets = [fetch(ruleset_id) for ruleset_id in update_ids]
    bypass = effective_update_bypass(update_rulesets) if update_ids else "always"

    if merge == "yolo":
        identity = _common.gh_json("api", "user")
        login = identity.get("login") if isinstance(identity, dict) else None
        if not isinstance(login, str):
            return _common.fail(CONTROL_PLANE_ERROR.format(branch=default_branch))
        if not has_valid_control_plane_codeowners(repo, default_branch, login):
            return _common.fail(CONTROL_PLANE_ERROR.format(branch=default_branch))
        if bypass != "pull_requests_only":
            return _common.fail(
                YOLO_BYPASS_ERROR.format(branch=default_branch, actual=bypass)
            )
        lifecycle = {
            rule_type: effective_update_bypass(
                [fetch(ruleset_id) for ruleset_id in ruleset_ids(rules, rule_type)]
            )
            if ruleset_ids(rules, rule_type)
            else "always"
            for rule_type in ("creation", "deletion")
        }
        unsafe_lifecycle = {
            rule_type: actual
            for rule_type, actual in lifecycle.items()
            if actual not in {"never", "pull_requests_only"}
        }
        if unsafe_lifecycle:
            actual = ", ".join(
                f"{rule_type}={level}" for rule_type, level in unsafe_lifecycle.items()
            )
            return _common.fail(
                YOLO_LIFECYCLE_ERROR.format(branch=default_branch, actual=actual)
            )
        pull_request_rulesets = [
            fetch(ruleset_id) for ruleset_id in ruleset_ids(rules, "pull_request")
        ]
        if not has_control_plane_review(pull_request_rulesets):
            return _common.fail(CONTROL_PLANE_ERROR.format(branch=default_branch))
        print(
            "Security preflight passed: yolo may merge pull requests, direct "
            f"pushes to '{default_branch}' are blocked, and control-plane "
            "changes require maintainer approval",
            flush=True,
        )
        return 0

    if bypass == "never":
        print(
            "Security preflight passed: bot cannot bypass the restrict-updates "
            f"ruleset on '{default_branch}'",
            flush=True,
        )
        return 0
    if any(
        isinstance(ruleset, dict) and ruleset.get("current_user_can_bypass") != "never"
        for ruleset in update_rulesets
    ):
        return _common.fail(BYPASS_ERROR.format(branch=default_branch))

    # The listing, or every update ruleset in it, could not be read: fall back
    # to requiring that the branch is protected at all.
    branch = _common.gh_json("api", f"repos/{repo}/branches/{default_branch}")
    if branch.get("protected") is not True:
        return _common.fail(UNPROTECTED_ERROR.format(branch=default_branch))
    print(
        f"Security preflight passed: default branch '{default_branch}' is protected",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    _common.run(main)
