"""Refuse to run unless the default branch is protected against the bot itself.

Shared verbatim by both harness actions (``claude/``, ``codex/``), where the
step is ``id: security`` and a non-zero exit is what the "Report failure" step
reads off ``steps.security.outcome``.

The step runs with the bot's own token, so ``current_user_can_bypass`` on each
applying ruleset is GitHub's answer to "can this bot bypass?" — teams, custom
roles, and org/enterprise-sourced rulesets are all evaluated server-side. One
update rule the bot cannot bypass proves the bot cannot update the branch. If
update rules exist but the bot can bypass every one, the merge restriction
provably does not restrict the bot and the run aborts. So does a branch with no
update rule at all: required reviews don't restrict the bot, which holds write,
so its own approval counts on a pull request someone else opened, and it can
then merge that pull request.

Decisions this encodes:

- A ruleset whose ``current_user_can_bypass`` cannot be read — the call
  fails, or the body doesn't carry the field — proves nothing either way, so
  it neither blocks nor counts as bypassable; the run falls through to the
  ``.protected`` floor if no other update rule settles it.
- Any readable value other than ``never`` counts as bypassable, JSON ``null``
  included — an answer that isn't "never" is not a restriction.
- A rules listing that cannot be read, or that comes back as anything but an
  array of pages, falls through to the ``.protected`` floor too, so a GitHub
  outage doesn't take the gate down with it. A listing that reads cleanly is
  an answer, even when no entry in it is a usable update rule.

Inputs (env): ``GITHUB_REPOSITORY`` (from Actions), plus the bot's
``GITHUB_TOKEN``, which reaches ``gh`` through the environment.
"""

from __future__ import annotations

from typing import Any

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


def update_ruleset_ids(rules: list[Any]) -> list[int]:
    """The ids of the rulesets contributing an ``update`` rule, deduped.

    A ruleset can contribute several rules to one branch, and only the
    ``update`` ones restrict who may move the branch. Anything that is not a
    rule naming both a type and a ruleset id contributes nothing, which is what
    the jq ``select`` this replaced did.
    """
    return sorted(
        {
            rule["ruleset_id"]
            for rule in rules
            if isinstance(rule, dict)
            and rule.get("type") == "update"
            and isinstance(rule.get("ruleset_id"), int)
        }
    )


def main() -> int:
    repo = _common.require_env("GITHUB_REPOSITORY")["GITHUB_REPOSITORY"]

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
        rules = None
    ruleset_ids = update_ruleset_ids(rules or [])
    if rules is not None and not ruleset_ids:
        return _common.fail(NO_RULESET_ERROR.format(branch=default_branch))

    bypassable = False
    for ruleset_id in ruleset_ids:
        try:
            ruleset = _common.gh_json("api", f"repos/{repo}/rulesets/{ruleset_id}")
        except _common.GH_READ_FAILED:
            continue
        # A body without the field, such as an error object under a 200, is
        # as unread as a failed call.
        if not isinstance(ruleset, dict) or "current_user_can_bypass" not in ruleset:
            continue
        if ruleset["current_user_can_bypass"] == "never":
            print(
                "Security preflight passed: bot cannot bypass the restrict-updates "
                f"ruleset on '{default_branch}'",
                flush=True,
            )
            return 0
        bypassable = True

    if bypassable:
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
