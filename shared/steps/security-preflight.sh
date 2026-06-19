#!/usr/bin/env bash
# Shared preflight: refuse to run unless the default branch is protected.
# Without branch protection the bot could merge its own PRs — the primary
# security boundary (see docs/security-model.md). Shared verbatim by all three
# harness actions (claude/, claude-interactive/, codex/).
#
# Inputs (env): GITHUB_TOKEN (for gh), GITHUB_REPOSITORY (from Actions).
set -eo pipefail

DEFAULT_BRANCH=$(gh api "repos/${GITHUB_REPOSITORY}" --jq '.default_branch')
PROTECTED=$(gh api "repos/${GITHUB_REPOSITORY}/branches/${DEFAULT_BRANCH}" --jq '.protected')
if [ "$PROTECTED" != "true" ]; then
  echo "::error::Default branch '${DEFAULT_BRANCH}' is NOT protected. Without branch protection, the bot can merge PRs without review. Add a branch protection rule or ruleset before using Tend. See docs/security-model.md in the Tend repo."
  exit 1
fi
echo "Security preflight passed: default branch '${DEFAULT_BRANCH}' is protected"
