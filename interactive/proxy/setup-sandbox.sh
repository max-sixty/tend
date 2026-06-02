#!/usr/bin/env bash
# Runner-side setup for credential isolation (gh/git cut).
#
# Runs as the privileged `runner` user. Stands up everything needed to run the
# agent as a separate, non-sudo `tend-sandbox` user whose only path to an
# authenticated GitHub call is a local mitmproxy that holds the real token:
#
#   1. Create the tend-sandbox user (no sudo, distinct UID).
#   2. Neutralize the bot PAT actions/checkout persists for git — otherwise the
#      sandbox reads it off disk and isolation is moot.
#   3. Hand the checkout to tend-sandbox (and make the path traversable) so the
#      agent can edit and commit.
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
# Run dir (sentinels, PTY log, wrapper) lives in the sandbox's own home so it
# can write there freely; the runner reads it via the 0755 home path. (Under
# RUNNER_TEMP the sandbox can't create it — that dir is runner-owned.)
TEND_RUN_DIR="${AGENT_HOME}/run"
CONFDIR="${RUNNER_TEMP}/tend-proxy"

log() { echo "[setup-sandbox] $*"; }

if [ -z "${TEND_GH_TOKEN:-}" ]; then
  echo "::error::TEND_GH_TOKEN is unset; cannot start the credential proxy"
  exit 1
fi

# 1. Non-sudo sandbox user. -m gives it /home/tend-sandbox (0755, so the
#    runner can still read the session logs it writes).
if ! id "$SANDBOX" >/dev/null 2>&1; then
  sudo useradd -m -s /usr/bin/bash "$SANDBOX"
fi
log "user $SANDBOX uid=$(id -u "$SANDBOX")"

# 2. Neutralize the credential actions/checkout persisted for git. Modern
#    checkout stores it in an external file referenced by an includeIf, not as
#    a plain extraheader — so drop the extraheader (older form), remove any
#    includeIf sections, and lock the external credential files to runner-only.
#    Belt: even if an include survives, git can't read a 0600 runner-owned file
#    as the sandbox, so no credential reaches the agent. The proxy is the only
#    authenticated path left.
git -C "$GITHUB_WORKSPACE" config --local --unset-all \
  'http.https://github.com/.extraheader' 2>/dev/null || true
while read -r key; do
  [ -n "$key" ] || continue
  git -C "$GITHUB_WORKSPACE" config --local --remove-section "${key%.path}" 2>/dev/null || true
done < <(git -C "$GITHUB_WORKSPACE" config --local --name-only --get-regexp '^includeif\.' 2>/dev/null || true)
sudo find "$RUNNER_TEMP" -maxdepth 1 -name 'git-credentials-*' -exec chmod 600 {} + 2>/dev/null || true
log "neutralized persisted git credentials"

# 3. Make the path to the workspace traversable by the sandbox, then hand it
#    the checkout so the agent can edit and commit. The sandbox owns the tree,
#    so no safe.directory entry is needed. o+x grants traversal only (not read)
#    on the runner's home — fine on a single-use runner.
sudo chmod o+x /home/runner /home/runner/work "$(dirname "$GITHUB_WORKSPACE")"
sudo chown -R "${SANDBOX}:${SANDBOX}" "$GITHUB_WORKSPACE"
sudo -u "$SANDBOX" test -r "$GITHUB_WORKSPACE/.git/config" \
  || { echo "::error::sandbox cannot access the workspace at $GITHUB_WORKSPACE"; exit 1; }
log "workspace handed to $SANDBOX"

# Shared dir the sandbox writes (sentinels, PTY log, wrapper) and the runner
# reads. Sandbox-owned so its hooks can touch the sentinels; the runner
# supervisor polls them via the 0755 home/temp path.
sudo -u "$SANDBOX" mkdir -p "$TEND_RUN_DIR"
log "run dir $TEND_RUN_DIR"

# 4. Start the injecting proxy. It inherits TEND_GH_TOKEN from this shell; the
#    token never leaves this runner-owned process. confdir is 0700 runner-only
#    so the sandbox can't read the CA private key (it only needs the public
#    cert, added to the system trust store below).
mkdir -p "$CONFDIR"
chmod 700 "$CONFDIR"
# Warm the uvx cache first so the backgrounded launch starts immediately and
# the readiness wait below measures startup, not a cold dependency resolve.
# Pinned + UV_CACHE_DIR (set by the action) point at the actions/cache-backed
# dir, so this is a fast restore after the first run.
MITMPROXY="mitmproxy==${MITMPROXY_VERSION:-12.2.1}"
uvx --from "$MITMPROXY" mitmdump --version >/dev/null
log "starting proxy"
nohup uvx --from "$MITMPROXY" mitmdump \
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
  echo "::error::proxy CA not generated after 30s; mitmdump failed to start"
  cat "${RUNNER_TEMP}/tend-proxy.log" || true
  exit 1
fi

# System-trust the proxy CA so the sandbox's gh (Go) and git (libcurl) accept
# the intercepted GitHub TLS. Only the public cert is exported.
sudo cp "${CONFDIR}/mitmproxy-ca-cert.pem" /usr/local/share/ca-certificates/tend-proxy.crt
sudo update-ca-certificates >/dev/null
log "proxy up at $PROXY_URL; CA trusted"

{
  echo "SANDBOX=${SANDBOX}"
  echo "AGENT_HOME=${AGENT_HOME}"
  echo "PROXY_URL=${PROXY_URL}"
  echo "TEND_RUN_DIR=${TEND_RUN_DIR}"
} >>"$GITHUB_ENV"

log "done; agent runs as ${SANDBOX}, GitHub auth via the proxy"
