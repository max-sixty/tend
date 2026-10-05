#!/usr/bin/env bash
# Install this immutable action's Tend package, with its frozen dependencies.
# The runner venv survives sandbox disposal for result processing. The sandbox
# venv is outside the runner's home view and read-only inside the agent unit.
set -euo pipefail

: "${ACTION_PATH:?ACTION_PATH is required}"
: "${UV_INSTALL_DIR:?UV_INSTALL_DIR is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"

# A fresh job-local cache avoids reusing consumer-controlled runner caches.
runtime_cache=$(mktemp -d "${TEND_PRIVATE_DIR:-$RUNNER_TEMP}/tend-runtime-cache.XXXXXX")
install_package() {
  local runtime_env=$1
  # Consumer UV_* / Python controls cannot alter the candidate or its lock.
  env -i HOME="$HOME" PATH=/usr/bin:/bin \
    UV_PROJECT_ENVIRONMENT="$runtime_env" UV_CACHE_DIR="$runtime_cache" \
    "$UV_INSTALL_DIR/uv" sync --no-config --no-python-downloads \
      --python /usr/bin/python3 --project "$ACTION_PATH" --package tend \
      --frozen --no-dev --no-editable --link-mode copy
  chmod -R a+rX "$runtime_env"
}
install_package "$RUNNER_TEMP/tend-python"
# Later always() steps can diagnose and dispose a failed sandbox installation.
printf 'TEND_RUNTIME_PYTHON=%s/bin/python\n' "$RUNNER_TEMP/tend-python" >> "$GITHUB_ENV"
if [[ -n "${TEND_RUNTIME_ROOT:-}" ]]; then
  install_package "$TEND_RUNTIME_ROOT/venv"
fi
