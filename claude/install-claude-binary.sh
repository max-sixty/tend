#!/usr/bin/env bash
# Install the claude binary DIRECTLY into the sandbox user's home, so there is
# no ~200 MB `cp -a` from the runner. It carries absolute-path references into
# ~/.local/share, so it must be installed in place — installing as runner and
# moving breaks it. The installer fetches over the direct network (no proxy env
# here). Used by the Claude harness action.
#
# Inputs (env): CLAUDE_VERSION (claude binary version), SANDBOX and AGENT_HOME
# (exported by setup_sandbox.py via $GITHUB_ENV).
set -eo pipefail

# XDG_* pinned under the sandbox home: the runner exports
# XDG_CONFIG_HOME=/home/runner/.config (leaks through sudo), which the
# sandbox UID can't write. Pin all four base dirs so the installer (and
# any XDG-aware tool the agent later runs) lands under $AGENT_HOME.
# Install fetches go direct (no proxy env here), so this step does not
# source $AGENT_ENV_FILE.
sudo -u "$SANDBOX" env HOME="$AGENT_HOME" CLAUDE_VERSION="$CLAUDE_VERSION" \
  XDG_CONFIG_HOME="$AGENT_HOME/.config" \
  XDG_CACHE_HOME="$AGENT_HOME/.cache" \
  XDG_DATA_HOME="$AGENT_HOME/.local/share" \
  XDG_STATE_HOME="$AGENT_HOME/.local/state" \
  bash <<'EOF'
set -euo pipefail
curl -fsSL https://claude.ai/install.sh | bash -s -- "$CLAUDE_VERSION"
EOF
sudo -u "$SANDBOX" "$AGENT_HOME/.local/bin/claude" --version
