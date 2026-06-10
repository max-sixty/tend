#!/usr/bin/env bash
# Runner-side setup for credential isolation.
#
# Runs as the privileged `runner` user. Stands up everything needed to run the
# agent as a separate, non-sudo `tend-sandbox` user whose only path to an
# authenticated GitHub *or* Anthropic call is a local mitmproxy that holds the
# real secrets:
#
#   1. Create the tend-sandbox user (no sudo, distinct UID).
#   2. Neutralize the bot PAT actions/checkout persists for git — otherwise the
#      sandbox reads it off disk and isolation is moot.
#   3. Hand the checkout to tend-sandbox (and make the path traversable) so the
#      agent can edit and commit.
#   4. Start the injecting proxy (holds the real GitHub + Anthropic credentials
#      in its own memory) and system-trust its CA so the sandbox's gh/git accept
#      the intercepted TLS. (claude is Node and uses its own CA bundle, so the
#      agent step also points NODE_EXTRA_CA_CERTS at the exported PROXY_CA_CERT.)
#
# Exports for later steps via $GITHUB_ENV: SANDBOX, AGENT_HOME, PROXY_URL,
# TEND_RUN_DIR, PROXY_CA_CERT, AGENT_ANTHROPIC_ENV.
#
# Inputs (env): TEND_GH_TOKEN (real PAT), TEND_ANTHROPIC_OAUTH_TOKEN and/or
# TEND_ANTHROPIC_API_KEY (real Anthropic credential, injected for
# api.anthropic.com), ACTION_PATH (this action's checkout), MITMPROXY_VERSION
# (pinned mitmproxy version). GITHUB_WORKSPACE / RUNNER_TEMP / UV_CACHE_DIR come
# from Actions.
set -euo pipefail

SANDBOX=tend-sandbox
AGENT_HOME="/home/${SANDBOX}"
PROXY_PORT=8899
PROXY_URL="http://127.0.0.1:${PROXY_PORT}"
# Public CA cert the proxy generates; system-trusted below for gh/git and
# pointed at by NODE_EXTRA_CA_CERTS for claude. World-readable so the sandbox
# (a different UID) can read it.
PROXY_CA_CERT=/usr/local/share/ca-certificates/tend-proxy.crt
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
if [ -z "${TEND_ANTHROPIC_OAUTH_TOKEN:-}" ] && [ -z "${TEND_ANTHROPIC_API_KEY:-}" ]; then
  echo "::error::No Anthropic credential set (TEND_ANTHROPIC_OAUTH_TOKEN or TEND_ANTHROPIC_API_KEY); cannot start the credential proxy"
  exit 1
fi
if [ -z "${MITMPROXY_VERSION:-}" ]; then
  echo "::error::MITMPROXY_VERSION is unset; the action must pin it"
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
#    a plain extraheader. So: drop the extraheader (older form); unset each
#    includeIf key (--unset on the FULL key — --remove-section can't match the
#    dotted subsection name); and DELETE the external credential files. Deleting
#    (not chmod) is load-bearing: the PAT is gone from disk, and git silently
#    skips a now-missing include — whereas an unreadable include is *fatal* and
#    would break every agent git operation. The proxy is the only auth path left.
git -C "$GITHUB_WORKSPACE" config --local --unset-all \
  'http.https://github.com/.extraheader' 2>/dev/null || true
while read -r key; do
  [ -n "$key" ] || continue
  git -C "$GITHUB_WORKSPACE" config --local --unset "$key" 2>/dev/null || true
done < <(git -C "$GITHUB_WORKSPACE" config --local --name-only --get-regexp '^includeif\.' 2>/dev/null || true)
sudo find "$RUNNER_TEMP" -maxdepth 2 -name 'git-credentials-*' -delete 2>/dev/null || true

# Verify the strip actually worked (the load-bearing security step): fail loudly
# if any GitHub credential still resolves in the workspace config. The repo rule
# is that an unhandled format fails with a clear error, not silently.
if git -C "$GITHUB_WORKSPACE" config --local --list 2>/dev/null \
     | grep -qiE 'extraheader=|^includeif\.gitdir'; then
  echo "::error::failed to neutralize the persisted git credential in $GITHUB_WORKSPACE/.git/config"
  git -C "$GITHUB_WORKSPACE" config --local --list | grep -iE 'extraheader=|^includeif\.gitdir' || true
  exit 1
fi
log "neutralized persisted git credentials"

# 3. Make the path to the workspace traversable by the sandbox, then hand it
#    the checkout so the agent can edit and commit. The sandbox owns the tree,
#    so no safe.directory entry is needed. Grant o+x (traversal only, not read)
#    on every ancestor of the workspace — derived, not hard-coded to /home/runner,
#    so it works wherever the runner places the checkout. Fine on a single-use runner.
parent="$(dirname "$GITHUB_WORKSPACE")"
while [ "$parent" != "/" ]; do
  sudo chmod o+x "$parent" 2>/dev/null || true
  parent="$(dirname "$parent")"
done
sudo chown -R "${SANDBOX}:${SANDBOX}" "$GITHUB_WORKSPACE"
sudo -u "$SANDBOX" test -r "$GITHUB_WORKSPACE/.git/config" \
  || { echo "::error::sandbox cannot access the workspace at $GITHUB_WORKSPACE"; exit 1; }
log "workspace handed to $SANDBOX"

# Shared dir the sandbox writes (sentinels, PTY log, wrapper) and the runner
# reads. Sandbox-owned so its hooks can touch the sentinels; the runner
# supervisor polls them via the 0755 home/temp path.
sudo -u "$SANDBOX" mkdir -p "$TEND_RUN_DIR"
log "run dir $TEND_RUN_DIR"

# 4. Start the injecting proxy. It inherits the real GitHub + Anthropic
#    credentials from this shell; they never leave this runner-owned process.
#    confdir is 0700 runner-only so the sandbox can't read the CA private key
#    (it only needs the public cert, added to the system trust store below).
mkdir -p "$CONFDIR"
chmod 700 "$CONFDIR"
# Warm the uvx cache first so the backgrounded launch starts immediately and
# the readiness wait below measures startup, not a cold dependency resolve.
# Pinned + UV_CACHE_DIR (set by the action) point at the actions/cache-backed
# dir, so this is a fast restore after the first run.
MITMPROXY="mitmproxy==${MITMPROXY_VERSION}"
uvx --from "$MITMPROXY" mitmdump --version >/dev/null
log "starting proxy"
nohup uvx --from "$MITMPROXY" mitmdump \
  -s "${ACTION_PATH}/proxy/inject_credentials.py" \
  --listen-host 127.0.0.1 --listen-port "$PROXY_PORT" \
  --set confdir="$CONFDIR" \
  --allow-hosts '^((api\.|codeload\.|uploads\.)?github\.com|api\.anthropic\.com)(:[0-9]+)?$' \
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
# the intercepted GitHub TLS. Only the public cert is exported. claude (Node)
# ignores the system store, so the agent step points NODE_EXTRA_CA_CERTS at this
# same cert for the intercepted api.anthropic.com TLS.
sudo cp "${CONFDIR}/mitmproxy-ca-cert.pem" "$PROXY_CA_CERT"
sudo update-ca-certificates >/dev/null
log "proxy up at $PROXY_URL; CA trusted"

# The agent runs `claude` in the SAME auth mode as the real credential (so it
# emits the right headers) but with a DUMMY secret; the proxy swaps in the real
# one for api.anthropic.com. Export the dummy as a ready-to-use `env` assignment
# for the agent steps (single source of truth for the scheme + value). The
# `tendproxydummy` marker lets the smoke prove the real secret never arrives.
if [ -n "${TEND_ANTHROPIC_OAUTH_TOKEN:-}" ]; then
  AGENT_ANTHROPIC_ENV="CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-tendproxydummy0000000000000000000000000000"
else
  AGENT_ANTHROPIC_ENV="ANTHROPIC_API_KEY=sk-ant-api03-tendproxydummy0000000000000000000000000000"
fi

{
  echo "SANDBOX=${SANDBOX}"
  echo "AGENT_HOME=${AGENT_HOME}"
  echo "PROXY_URL=${PROXY_URL}"
  echo "TEND_RUN_DIR=${TEND_RUN_DIR}"
  echo "PROXY_CA_CERT=${PROXY_CA_CERT}"
  echo "AGENT_ANTHROPIC_ENV=${AGENT_ANTHROPIC_ENV}"
} >>"$GITHUB_ENV"

log "done; agent runs as ${SANDBOX}, GitHub + Anthropic auth via the proxy"
