#!/usr/bin/env bash
# Runner-side setup for credential isolation (gh/git cut).
#
# Runs as the privileged `runner` user. Stands up everything needed to run the
# agent as a separate, non-sudo `tend-sandbox` user whose only path to an
# authenticated GitHub call is a local mitmproxy that holds the real token:
#
#   1. Create the tend-sandbox user (no sudo, distinct UID).
#   2. Strip the bot PAT that `actions/checkout` persisted into .git/config —
#      otherwise the sandbox reads it straight off disk and isolation is moot.
#   3. Hand the checkout to tend-sandbox so the agent can edit and commit.
#   4. Start the injecting proxy (holds TEND_GH_TOKEN in its own memory) and
#      system-trust its CA so the sandbox's gh/git accept the intercepted TLS.
#
# Exports for later steps via $GITHUB_ENV: SANDBOX, AGENT_HOME, PROXY_URL,
# TEND_RUN_DIR.
#
# Inputs (env): TEND_GH_TOKEN (real PAT), ACTION_PATH (this action's checkout),
# PROXY_PORT (default 8899). GITHUB_WORKSPACE / RUNNER_TEMP come from Actions.
set -euo pipefail

SANDBOX=tend-sandbox
AGENT_HOME="/home/${SANDBOX}"
PROXY_PORT="${PROXY_PORT:-8899}"
PROXY_URL="http://127.0.0.1:${PROXY_PORT}"
TEND_RUN_DIR="${RUNNER_TEMP}/tend-sandbox"
CONFDIR="${RUNNER_TEMP}/tend-proxy"

if [ -z "${TEND_GH_TOKEN:-}" ]; then
  echo "::error::TEND_GH_TOKEN is unset; cannot start the credential proxy"
  exit 1
fi

# 1. Non-sudo sandbox user. -m gives it /home/tend-sandbox (0755, so the
#    runner can still read the session logs it writes).
if ! id "$SANDBOX" >/dev/null 2>&1; then
  sudo useradd -m -s /usr/bin/bash "$SANDBOX"
fi

# 2. Strip the PAT that `actions/checkout` (persist-credentials: true) wrote
#    into .git/config. After this the only authenticated path to GitHub is the
#    proxy. Run before the chown while the file is still runner-owned.
git -C "$GITHUB_WORKSPACE" config --unset-all \
  'http.https://github.com/.extraheader' 2>/dev/null || true

# 3. Hand the checkout to the sandbox and mark it a safe git directory for it.
sudo chown -R "${SANDBOX}:${SANDBOX}" "$GITHUB_WORKSPACE"
sudo -u "$SANDBOX" git config --global --add safe.directory "$GITHUB_WORKSPACE"

# Shared dir the sandbox writes (sentinels, PTY log, wrapper) and the runner
# reads. Sandbox-owned so its hooks can touch the sentinels; 0755 so the
# runner supervisor can poll them.
sudo -u "$SANDBOX" mkdir -p "$TEND_RUN_DIR"

# 4. Start the injecting proxy. It inherits TEND_GH_TOKEN from this shell; the
#    token never leaves this runner-owned process. confdir is 0700 runner-only
#    so the sandbox can't read the CA private key (it only needs the public
#    cert, added to the system trust store below).
mkdir -p "$CONFDIR"
chmod 700 "$CONFDIR"
# Warm the uvx cache first so the backgrounded launch starts immediately and
# the readiness wait below measures startup, not a cold dependency resolve.
uvx --from mitmproxy mitmdump --version >/dev/null
nohup uvx --from mitmproxy mitmdump \
  -s "${ACTION_PATH}/proxy/github_auth.py" \
  --listen-host 127.0.0.1 --listen-port "$PROXY_PORT" \
  --set confdir="$CONFDIR" \
  --allow-hosts 'github\.com' \
  </dev/null >"${RUNNER_TEMP}/tend-proxy.log" 2>&1 &
echo $! >"${RUNNER_TEMP}/tend-proxy.pid"
disown

# Wait for the proxy to generate its CA (proof it's listening).
for _ in $(seq 1 60); do
  [ -f "${CONFDIR}/mitmproxy-ca-cert.pem" ] && break
  sleep 0.5
done
if [ ! -f "${CONFDIR}/mitmproxy-ca-cert.pem" ]; then
  echo "::error::proxy CA not generated after 20s; mitmdump failed to start"
  cat "${RUNNER_TEMP}/tend-proxy.log" || true
  exit 1
fi

# System-trust the proxy CA so the sandbox's gh (Go) and git (libcurl) accept
# the intercepted GitHub TLS. Only the public cert is exported.
sudo cp "${CONFDIR}/mitmproxy-ca-cert.pem" /usr/local/share/ca-certificates/tend-proxy.crt
sudo update-ca-certificates >/dev/null

{
  echo "SANDBOX=${SANDBOX}"
  echo "AGENT_HOME=${AGENT_HOME}"
  echo "PROXY_URL=${PROXY_URL}"
  echo "TEND_RUN_DIR=${TEND_RUN_DIR}"
} >>"$GITHUB_ENV"

echo "Sandbox ready: agent runs as ${SANDBOX}; GitHub auth via ${PROXY_URL}"
