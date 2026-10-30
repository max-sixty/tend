#!/usr/bin/env bash
# Install the pinned Tend package into exactly one caller-owned environment.
# Runner setup survives result processing; sandbox setup is disposed with the unit.
set -euo pipefail

: "${ACTION_PATH:?ACTION_PATH is required}"
: "${UV_INSTALL_DIR:?UV_INSTALL_DIR is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_PATH:?GITHUB_PATH is required}"

case "${1:-}" in
  runner)
    : "${GITHUB_ENV:?GITHUB_ENV is required}"
    runtime_env="$RUNNER_TEMP/tend-python"
    ;;
  sandbox)
    : "${TEND_RUNTIME_PYTHON:?Run max-sixty/tend/setup-runtime at the same ref before this action}"
    : "${TEND_RUNTIME_ROOT:?TEND_RUNTIME_ROOT is required}"
    runtime_env="$TEND_RUNTIME_ROOT/venv"
    # Cleanup and reporting need this command even if sandbox installation fails.
    printf '%s/tend-python/commands\n' "$RUNNER_TEMP" >> "$GITHUB_PATH"
    ;;
  *)
    echo "usage: install-runtime.sh runner|sandbox" >&2
    exit 2
    ;;
esac

# A fresh job-local cache avoids reusing consumer-controlled runner caches.
runtime_cache=$(mktemp -d "${TEND_PRIVATE_DIR:-$RUNNER_TEMP}/tend-runtime-cache.XXXXXX")
# Consumer UV_* / Python controls cannot alter the candidate or its lock.
env -i HOME="$HOME" PATH=/usr/bin:/bin \
  UV_PROJECT_ENVIRONMENT="$runtime_env" UV_CACHE_DIR="$runtime_cache" \
  "$UV_INSTALL_DIR/uv" sync --no-config --no-python-downloads \
    --python /usr/bin/python3 --project "$ACTION_PATH" --package tend \
    --frozen --no-dev --no-editable --link-mode copy
mkdir -p "$runtime_env/commands"
install -m 755 "$ACTION_PATH/shared/steps/tend" "$runtime_env/commands/tend"
chmod -R a+rX "$runtime_env"

if [[ "$1" == runner ]]; then
  # The sandbox gets its own command only inside the protected agent unit.
  printf '%s/tend-python/commands\n' "$RUNNER_TEMP" >> "$GITHUB_PATH"
  printf 'TEND_RUNTIME_PYTHON=%s/bin/python\n' "$runtime_env" >> "$GITHUB_ENV"
fi
