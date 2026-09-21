#!/usr/bin/env bash
# Hosted-runner integration test for the copy-on-write view, the sandbox UID and
# the PATH boundary. Commands are separate Actions steps because
# GITHUB_PATH/GITHUB_ENV affect only later steps.
#
# It checks what needs a real kernel, a second uid and an Actions runner: the
# agent works in the job's own checkout and home and writes wherever the runner
# could; the host is byte-for-byte unchanged afterwards; the runner's own files
# read back empty; and `ACTIONS_*`/`INPUT_*` never reach the harness.
set -euo pipefail

BOT_LOGIN=tend-agent
BOT_ID=4242
# What `event_checkout` checks out in `base` mode, which it also sets an
# upstream for. On `pull_request` GITHUB_REF_NAME is `<number>/merge`, a ref
# origin publishes under `refs/pull/` and never as a branch; GITHUB_BASE_REF is
# the base branch. On the push to main it is the other way round.
BASE_BRANCH="${GITHUB_BASE_REF:-$GITHUB_REF_NAME}"

set_inputs() {
  export TEND_GH_TOKEN=dummy
  export TEND_ANTHROPIC_OAUTH_TOKEN=dummy
  export ACTION_PATH="$TEND_TEST_ACTION_PATH"
  export TEND_UV_DIR="$RUNNER_TEMP/tend-uv"
  export UV_CACHE_DIR="$RUNNER_TEMP/tend-mitmproxy-uv"
}

plant() {
  local bin seeded shared
  bin="$HOME/.cargo-install/tend-probe/bin"
  seeded="$HOME/.tend-seeded/bin"
  shared="/opt/tend-sandbox-test-$GITHUB_RUN_ID/bin"
  # What a consumer's `setup:` leaves: a warm cache, a root-owned directory (a
  # `docker run -v` artefact), and a directory to rename (`redirect_dir=on`).
  TEND_WARM_CACHE="$HOME/.tend-warm-cache"
  mkdir -p "$TEND_WARM_CACHE/registry"
  printf 'warm-cache\n' >"$TEND_WARM_CACHE/registry/warm"
  sudo install -d -m 755 -o root -g root "$TEND_WARM_CACHE/root-owned"
  printf 'root-owned\n' | sudo tee "$TEND_WARM_CACHE/root-owned/planted" >/dev/null
  mkdir -p "$TEND_WARM_CACHE/rename-me/inner"
  TEND_WARM_TREE="$GITHUB_WORKSPACE/.tend-warm-tree"
  mkdir -p "$TEND_WARM_TREE"
  printf 'built\n' >"$TEND_WARM_TREE/artifact"
  TEND_HOST_SUM=$(host_checksum)
  test -n "$TEND_HOST_SUM"
  TEND_HOST_HEAD=$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)

  TEND_TEST_ACTION_PATH=$(mktemp -d /var/tmp/tend-test-action.XXXXXX)
  chmod 755 "$TEND_TEST_ACTION_PATH"
  cp -a "$GITHUB_WORKSPACE/claude" "$GITHUB_WORKSPACE/codex" \
    "$GITHUB_WORKSPACE/proxy" \
    "$GITHUB_WORKSPACE/shared" \
    "$TEND_TEST_ACTION_PATH/"

  mkdir -p "$bin" "$seeded"
  printf '#!/bin/sh\necho probe\n' >"$bin/tend-probe"
  printf '#!/bin/sh\necho runner-home-uv\n' >"$bin/uv"
  chmod +x "$bin/tend-probe" "$bin/uv"
  # A tool `setup:` installed under the runner's home.
  printf '#!/bin/sh\necho runner-seed\n' >"$seeded/tend-seeded"
  chmod +x "$seeded/tend-seeded"
  printf '#!/bin/sh\necho system-fallback\n' \
    | sudo tee /usr/local/bin/tend-probe >/dev/null
  sudo chmod +x /usr/local/bin/tend-probe
  sudo install -d -m 755 "$shared"
  printf '#!/bin/sh\necho shared\n' | sudo tee "$shared/tend-shared" >/dev/null
  printf '#!/bin/sh\necho consumer-uv\n' | sudo tee "$shared/uv" >/dev/null
  sudo chmod +x "$shared/tend-shared" "$shared/uv"
  # setup_sandbox.py must capture this PATH entry as tool data without resolving
  # its privileged utilities through a consumer-controlled directory.
  printf '#!/bin/sh\nexit 99\n' >"$bin/sudo"
  chmod +x "$bin/sudo"
  echo "$shared" >>"$GITHUB_PATH"
  echo "$seeded" >>"$GITHUB_PATH"
  echo "$bin" >>"$GITHUB_PATH"
  {
    echo "TEND_TEST_ACTION_PATH=$TEND_TEST_ACTION_PATH"
    echo "TEND_WARM_CACHE=$TEND_WARM_CACHE"
    echo "TEND_WARM_TREE=$TEND_WARM_TREE"
    echo "TEND_HOST_SUM=$TEND_HOST_SUM"
    echo "TEND_HOST_HEAD=$TEND_HOST_HEAD"
  } >> "$GITHUB_ENV"
}

# Every name, mode, owner and content under the two directories `setup:` made.
host_checksum() {
  {
    find "$TEND_WARM_CACHE" "$TEND_WARM_TREE" -printf '%P %m %U:%G %y\n' | sort
    find "$TEND_WARM_CACHE" "$TEND_WARM_TREE" -type f -exec sha256sum {} + | sort
  } | sha256sum
}

setup() {
  local action_run agent_path hostile_python hostile_site
  set_inputs
  # shellcheck disable=SC2088
  export TEND_SANDBOX_PATH='~/.tend-tilde/bin'
  export TEND_SANDBOX_ENV="TEND_FROM_SANDBOX_ENV=applied"
  MITMPROXY_VERSION=$(yq -e '.inputs.mitmproxy_version.default' claude/action.yaml)
  export MITMPROXY_VERSION
  UV_VERSION=$(yq -e '.inputs.uv_version.default' claude/action.yaml) \
    UV_INSTALL_DIR="$TEND_UV_DIR" bash shared/steps/install-uv.sh
  # The setup step receives both real credentials. Repository-controlled
  # Python and uv environment variables must not execute code before the
  # runner-owned script has established the sandbox boundary.
  hostile_python="$RUNNER_TEMP/tend-hostile-python"
  hostile_site="$RUNNER_TEMP/tend-hostile-site"
  mkdir -p "$hostile_site"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    "touch '$RUNNER_TEMP/uv-python-used'" \
    'exec /usr/bin/python3 "$@"' >"$hostile_python"
  chmod +x "$hostile_python"
  printf '%s\n' \
    'from pathlib import Path' \
    "Path('$RUNNER_TEMP/pythonpath-used').touch()" \
    >"$hostile_site/sitecustomize.py"
  export UV_PYTHON="$hostile_python"
  export PYTHONPATH="$hostile_site"
  # Exercise the composite action's entrypoint rather than calling the setup
  # script directly. The boundary depends on the PATH the action passes in.
  action_run=$(yq -er '.runs.steps[] | select(.name == "Set up credential-isolation sandbox") | .run' claude/action.yaml)
  action_run=${action_run//'${{ github.action_path }}'/"$TEND_TEST_ACTION_PATH/claude"}
  /usr/bin/bash --noprofile --norc -eo pipefail -c "$action_run" \
    | tee "$RUNNER_TEMP/setup.log"
  test ! -e "$RUNNER_TEMP/uv-python-used"
  test ! -e "$RUNNER_TEMP/pythonpath-used"

  agent_path=$(sed -n 's/^\[setup-sandbox\] sandbox PATH: //p' "$RUNNER_TEMP/setup.log")
  test -n "$agent_path"
  case ":$agent_path:" in
    *":$HOME/.tend-seeded/bin:"*) ;;
    *) echo "::error::a runner-home PATH entry was dropped: $agent_path"; exit 1 ;;
  esac
  case ":$agent_path:" in
    *:/home/tend-sandbox/.tend-tilde/bin:*) ;;
    *) echo "::error::sandbox_path ~ was not expanded: $agent_path"; exit 1 ;;
  esac
  rm "$HOME/.cargo-install/tend-probe/bin/sudo"
}

install_agent_uv() {
  local action_run harness private_action
  private_action=$(mktemp -d "$RUNNER_TEMP/tend-private-action.XXXXXX")
  mkdir -p "$private_action/claude" "$private_action/codex" \
    "$private_action/shared/steps"
  cp "$TEND_TEST_ACTION_PATH/shared/steps/install-uv.sh" \
    "$private_action/shared/steps/"
  if sudo -u "$SANDBOX" test -r "$private_action/shared/steps/install-uv.sh"; then
    echo "::error::private action fixture is readable by the sandbox user"
    exit 1
  fi
  UV_VERSION=$(yq -e '.inputs.uv_version.default' claude/action.yaml)
  export UV_VERSION
  for harness in claude codex; do
    action_run=$(yq -er '.runs.steps[] | select(.name == "Install agent uv fallback (sandbox)") | .run' "$harness/action.yaml")
    action_run=${action_run//'${{ github.action_path }}'/"$private_action/$harness"}
    /usr/bin/bash --noprofile --norc -eo pipefail -c "$action_run"
  done
  rm -rf "$private_action"
}

verify() {
  sudo -u "$SANDBOX" test -x "$TEND_AGENT_UV_DIR/uv"
  grep -q "^PATH=.*:${TEND_AGENT_UV_DIR}$" "$AGENT_ENV_FILE"
  grep -qx 'TMPDIR=/home/tend-sandbox/tmp' "$AGENT_ENV_FILE"
  grep -qx 'CLAUDE_CONFIG_DIR=/home/tend-sandbox/.claude' "$AGENT_ENV_FILE"
  grep -qx 'TEND_FROM_SANDBOX_ENV=applied' "$AGENT_ENV_FILE"
  sudo -u "$SANDBOX" test -w /home/tend-sandbox/tmp
  # Tend's own runner-side secrets, including the proxy's CA private key.
  test -f "$TEND_PRIVATE_DIR/tend-proxy/mitmproxy-ca.pem"
  if sudo -u "$SANDBOX" test -r "$TEND_PRIVATE_DIR/tend-proxy/mitmproxy-ca.pem"; then
    echo "::error::the proxy CA private key is readable by the sandbox user"
    exit 1
  fi
}

# This re-run exits before the proxy starts.
verify_refusals() {
  local rc empty_rc
  set_inputs
  export MITMPROXY_VERSION=0
  TEND_SANDBOX_ENV='GITHUB_WORKFLOW=spoofed-by-sandbox-env' \
    "$TEND_UV_DIR/uv" run --script proxy/setup_sandbox.py \
    >"$RUNNER_TEMP/refused.log" 2>&1 && rc=0 || rc=$?
  # Actions parses workflow commands out of step output; don't annotate this
  # passing refusal test with the error it deliberately provokes.
  sed 's/^::error::/refused: /' "$RUNNER_TEMP/refused.log"
  test "${rc:-0}" -ne 0
  grep -q '::error::sandbox_env may not set .GITHUB_WORKFLOW.' \
    "$RUNNER_TEMP/refused.log"

  GITHUB_WORKSPACE='' "$TEND_UV_DIR/uv" run --script proxy/setup_sandbox.py \
    >"$RUNNER_TEMP/empty-workspace.log" 2>&1 && empty_rc=0 || empty_rc=$?
  test "${empty_rc:-0}" -ne 0
  grep -q '::error::GITHUB_WORKSPACE must name' "$RUNNER_TEMP/empty-workspace.log"
}

verify_srt() {
  local claude_argv claude_env claude_stub codex_argv codex_env codex_stub dummy_token
  local github_output private_action probe_info probe_pid probe_port rc runner_summary
  local runner_owned setup_commands setup_proxy stream_json tool_root run_dir stub_bin
  github_output="$RUNNER_TEMP/srt-github-output"
  runner_summary="$RUNNER_TEMP/srt-step-summary"
  probe_info="$RUNNER_TEMP/srt-network-probe"
  tool_root="$TEND_TEST_ACTION_PATH/probe-bin"
  # Outside the view, so it outlives the process tree.
  run_dir=/home/tend-sandbox/run
  : > "$github_output"
  : > "$runner_summary"
  mkdir -p "$tool_root"
  printf '#!/bin/sh\necho tend-srt-tool-ok\n' > "$tool_root/probe"
  chmod +x "$tool_root/probe"

  private_action=$(mktemp -d "$RUNNER_TEMP/tend-private-runtime.XXXXXX")
  mkdir -p "$private_action/shared" "$private_action/codex"
  cp -R "$TEND_TEST_ACTION_PATH/shared/steps" "$private_action/shared/"
  cp "$TEND_TEST_ACTION_PATH/codex/runner.py" "$private_action/codex/"
  if sudo -u "$SANDBOX" test -r "$private_action/shared/steps/sandbox_runtime.mjs"; then
    echo "::error::private runtime fixture is readable by the sandbox user"
    exit 1
  fi

  # On the sandbox PATH through `sandbox_path:`'s `~` entry.
  stub_bin=/home/tend-sandbox/.tend-tilde/bin
  sudo -u "$SANDBOX" mkdir -p "$stub_bin"
  claude_stub="$stub_bin/claude"
  claude_env="$run_dir/tend-claude-env"
  claude_argv="$run_dir/tend-claude-argv"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    "env > '$claude_env'" \
    "printf '%s\\n' \"\$@\" > '$claude_argv'" \
    'sleep 300 &' \
    'printf "%s\n" "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"stub turn\"}]}}"' \
    'printf "%s\n" "{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false}"' \
    | sudo -u "$SANDBOX" tee "$claude_stub" >/dev/null
  sudo -u "$SANDBOX" chmod +x "$claude_stub"

  codex_stub="$stub_bin/codex-stub"
  codex_argv="$run_dir/tend-codex-argv"
  codex_env="$run_dir/tend-codex-env"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    "env > '$codex_env'" \
    "printf '%s\\n' \"\$@\" > '$codex_argv'" \
    "curl --fail --silent \"\$CODEX_PROXY_URL\" > '$run_dir/tend-codex-network'" \
    'tool_no_proxy=' \
    'tool_no_proxy_lower=' \
    'for arg in "$@"; do' \
    '  case "$arg" in' \
    '    shell_environment_policy.set.NO_PROXY=*) tool_no_proxy=${arg#*=} ;;' \
    '    shell_environment_policy.set.no_proxy=*) tool_no_proxy_lower=${arg#*=} ;;' \
    '  esac' \
    'done' \
    'tool_no_proxy=${tool_no_proxy#\"}; tool_no_proxy=${tool_no_proxy%\"}' \
    'tool_no_proxy_lower=${tool_no_proxy_lower#\"}; tool_no_proxy_lower=${tool_no_proxy_lower%\"}' \
    "printf 'tend-srt-local-ok\\n' > '$run_dir/tend-local-probe'" \
    "local_log='$run_dir/tend-local-server-log'" \
    "/usr/bin/python3 -u -m http.server 0 --bind 127.0.0.1 --directory '$run_dir' >\"\$local_log\" 2>&1 &" \
    'local_pid=$!' \
    'for _ in {1..50}; do grep -q " port [0-9]" "$local_log" && break; sleep 0.1; done' \
    'local_port=$(sed -n "s/.* port \\([0-9][0-9]*\\) .*/\\1/p" "$local_log")' \
    "NO_PROXY=\"\$tool_no_proxy\" no_proxy=\"\$tool_no_proxy_lower\" curl --fail --silent \"http://127.0.0.1:\$local_port/tend-local-probe\" > '$run_dir/tend-codex-local-network'" \
    'kill "$local_pid" 2>/dev/null || true' \
    'while [ "$#" -gt 0 ]; do' \
    '  if [ "$1" = --output-last-message ]; then' \
    '    printf "codex final\n" > "$2"' \
    '    break' \
    '  fi' \
    '  shift' \
    'done' \
    | sudo -u "$SANDBOX" tee "$codex_stub" >/dev/null
  sudo -u "$SANDBOX" chmod +x "$codex_stub"

  /usr/bin/python3 - "$probe_info" <<'PY' &
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"tend-srt-network-ok\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
Path(sys.argv[1]).write_text(str(server.server_port))
server.serve_forever()
PY
  probe_pid=$!
  for _ in {1..50}; do
    [ -s "$probe_info" ] && break
    sleep 0.1
  done
  probe_port=$(cat "$probe_info")

  dummy_token=$(sed -n 's/^GITHUB_TOKEN=//p' "$AGENT_ENV_FILE")
  test -n "$dummy_token"
  # Absent inside: the sandbox's /tmp is its own tmpfs.
  runner_owned="/tmp/tend-runner-owned-$GITHUB_RUN_ID"
  touch "$runner_owned"
  # SRT re-binds its default write path whenever the launching namespace has
  # it; world-writable, so a bind would let the sandbox's write through.
  install -d -m 1777 /tmp/claude
  # Asserted from inside, by the consumer's own `sandbox_setup:` hook.
  setup_commands=$(printf '%s\n' \
    'set -u' \
    '# The job is the agent: same paths, same home, same PATH.' \
    'test "$PWD" = "$GITHUB_WORKSPACE"' \
    'test "$HOME" = "$TEND_RUNNER_HOME"' \
    'test "$(cat "$TEND_WARM_CACHE/registry/warm")" = warm-cache' \
    'test "$(cat "$TEND_WARM_TREE/artifact")" = built' \
    'test "$(tend-seeded)" = runner-seed' \
    'test "$TEND_FROM_SANDBOX_ENV" = applied' \
    '# Writable in place, and across renames of a lower-layer directory.' \
    'printf "agent\n" > "$TEND_WARM_CACHE/registry/written-by-sandbox"' \
    'printf "AGENT-CACHE\n" > "$TEND_WARM_CACHE/registry/warm"' \
    'printf "agent\n" > "$TEND_WARM_TREE/written-by-sandbox"' \
    'mv "$TEND_WARM_CACHE/rename-me" "$TEND_WARM_CACHE/renamed"' \
    '# A directory only root can write is readable, and stays unwritable: the' \
    '# idmap swaps the two accounts and leaves every other id alone.' \
    'test "$(cat "$TEND_WARM_CACHE/root-owned/planted")" = root-owned' \
    'if { printf "x\n" > "$TEND_WARM_CACHE/root-owned/by-sandbox"; } 2>/dev/null; then exit 95; fi' \
    'ln "$TEND_WARM_CACHE/registry/warm" "$TEND_WARM_TREE/linked"' \
    'printf "sandbox\n" > "$GITHUB_WORKSPACE/.tend-srt-wrote-here"' \
    '# The runner-side halves of Tend, and the runner itself, stay out of reach.' \
    'if [ -r "$TEND_PRIVATE_DIR/tend-proxy/mitmproxy-ca.pem" ]; then exit 90; fi' \
    '# Swept for, so it catches the runner credentials moving.' \
    'if find "$TEND_RUNNER_HOME" -maxdepth 4 -size +0 -name ".credentials*" -print -o -maxdepth 4 -size +0 -name ".runner" -print 2>/dev/null | grep -q .; then exit 94; fi' \
    'if ls "$TEND_RUNTIME_ROOT/view" >/dev/null 2>&1; then exit 93; fi' \
    '# A $GITHUB_ENV export crosses as a variable; the path does not.' \
    'test -z "${GITHUB_ENV:-}"' \
    'test -z "${ACTIONS_RUNTIME_TOKEN:-}"' \
    '# Scratch: /tmp is a private tmpfs; /var/tmp belongs to the runner.' \
    'touch /tmp/tend-sandbox-scratch' \
    'if touch /var/tmp/tend-unscoped 2>/dev/null; then exit 91; fi' \
    "if [ -e '$runner_owned' ]; then exit 92; fi" \
    'mkdir -p /tmp/claude && touch /tmp/claude/tend-sandbox-wrote' \
    'touch "$TMPDIR/tend-scratch-probe"' \
    '# Hand the proof back outside the view, where the runner can read it.' \
    "printf '%s\n' \"\$HTTP_PROXY\" > $run_dir/tend-setup-proxy" \
    "stat -c %u:%g \"\$TEND_RUNNER_HOME\" > $run_dir/tend-view-owner" \
    "git config --global user.email > $run_dir/tend-git-identity" \
    "test \"\$GITHUB_TOKEN\" = \"$dummy_token\"")

  rm -rf -- "$RUNNER_TEMP/tend-agent-export"
  rc=0
  ACTION_PATH="$private_action" \
    TEND_HARNESS=claude \
    TEND_LIFECYCLE="$private_action/shared/steps/agent_lifecycle.py" \
    TEND_SANDBOX_SETUP="$setup_commands" \
    TEND_CHECKOUT_MODE=base \
    TEND_BASE_BRANCH="$BASE_BRANCH" \
    TEND_BOUNDARY_PROBE_URL="http://127.0.0.1:$probe_port/" \
    TEND_BOUNDARY_PROBE_EXECUTABLE="$tool_root/probe" \
    TEND_MODEL=stub-model TEND_ALLOWED_TOOLS='Bash,Read' \
    TEND_SYSTEM_PROMPT='stub system prompt' TEND_PROMPT='stub prompt' \
    TEND_TIMEOUT_SEC=60 SHOW_FULL_OUTPUT=true \
    BOT_NAME="$BOT_LOGIN" BOT_ID="$BOT_ID" CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0 \
    GITHUB_TOKEN=runner-token-must-not-cross \
    ACTIONS_RUNTIME_TOKEN=runner-service-must-not-cross \
    ACTIONS_RESULTS_URL=https://results.invalid/ \
    INPUT_GITHUB_TOKEN=runner-input-must-not-cross \
    TEND_JOB_ONLY=tend-job-env-marker \
    GITHUB_OUTPUT="$github_output" \
    GITHUB_STEP_SUMMARY="$runner_summary" \
    /usr/bin/python3 -E -s \
      "$TEND_TEST_ACTION_PATH/shared/steps/launch_sandbox_runtime.py" || rc=$?
  test "$rc" -eq 0
  grep -qx 'sandbox_reaped=true' "$github_output"
  # The job's environment crossed in a file `enter_view` removed, not on the
  # command line `sudo` logs — whose entry for this launch has to be there for
  # the marker's absence to mean anything.
  test ! -e "$TEND_PRIVATE_DIR/tend-launch-env"
  # Compared out here: the marker on `sudo`'s own command line would be logged.
  test "$(sudo -u "$SANDBOX" sed -n 's/^TEND_JOB_ONLY=//p' "$claude_env")" = \
    tend-job-env-marker
  # To a file first: under pipefail, `grep -q` quitting early SIGPIPEs the
  # journal and fails the pipeline whether or not it matched.
  # shellcheck disable=SC2024 # the runner writes the copy; only the read is root's
  sudo journalctl -q -t sudo --no-pager > "$RUNNER_TEMP/sudo-journal"
  grep -q 'enter_view\.py' "$RUNNER_TEMP/sudo-journal"
  if grep -q tend-job-env-marker "$RUNNER_TEMP/sudo-journal"; then
    echo "::error::the job environment reached sudo's log"
    exit 1
  fi
  stream_json=$(sed -n 's/^stream_json=//p' "$github_output")
  test -n "$stream_json"
  grep -q '"stub turn"' "$stream_json"
  grep -q 'stub turn' "$runner_summary"

  # The idmap presented the runner's home as the sandbox's own.
  test "$(sudo -u "$SANDBOX" cat "$run_dir/tend-view-owner")" = \
    "$(id -u "$SANDBOX"):$(id -g "$SANDBOX")"
  # Nothing the sandbox wrote reached the runner.
  test "$(host_checksum)" = "$TEND_HOST_SUM"
  test ! -e "$GITHUB_WORKSPACE/.tend-srt-wrote-here"
  test "$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)" = "$TEND_HOST_HEAD"
  test "$(sudo -u "$SANDBOX" cat "$run_dir/tend-git-identity")" = \
    "${BOT_ID}+${BOT_LOGIN}@users.noreply.github.com"
  setup_proxy=$(sudo -u "$SANDBOX" cat "$run_dir/tend-setup-proxy")
  test -n "$setup_proxy"
  test "$setup_proxy" != 'http://127.0.0.1:8899'
  sudo -u "$SANDBOX" grep -qxF "HTTP_PROXY=$setup_proxy" "$claude_env"
  sudo -u "$SANDBOX" grep -qx 'TMPDIR=/home/tend-sandbox/tmp' "$claude_env"
  sudo -u "$SANDBOX" grep -qxF "HOME=$HOME" "$claude_env"
  sudo -u "$SANDBOX" test -f /home/tend-sandbox/tmp/tend-scratch-probe
  test ! -e /tmp/tend-sandbox-scratch
  test ! -e /tmp/claude/tend-sandbox-wrote
  sudo -u "$SANDBOX" grep -qxF "GITHUB_TOKEN=$dummy_token" "$claude_env"
  if sudo -u "$SANDBOX" grep -q '^GITHUB_ENV=' "$claude_env"; then
    echo "::error::runner command-file path crossed into Claude"
    exit 1
  fi
  if sudo -u "$SANDBOX" grep -qE '^(ACTIONS_|INPUT_)' "$claude_env"; then
    echo "::error::a runner-internal namespace crossed into Claude"
    sudo -u "$SANDBOX" grep -E '^(ACTIONS_|INPUT_)' "$claude_env" | cut -d= -f1
    exit 1
  fi
  if sudo -u "$SANDBOX" grep -qE \
    'runner-token-must-not-cross|runner-service-must-not-cross|runner-input-must-not-cross' \
    "$claude_env"; then
    echo "::error::a runner credential crossed into Claude"
    exit 1
  fi
  for want in -p --model stub-model --permission-mode bypassPermissions \
    --allowedTools 'Bash,Read' --append-system-prompt 'stub system prompt' \
    --output-format stream-json --verbose 'stub prompt'; do
    sudo -u "$SANDBOX" grep -qxF -- "$want" "$claude_argv"
  done

  sudo rm -rf -- "$TEND_RUNTIME_ROOT/action" "$TEND_RUNTIME_ROOT/view"
  rm -rf -- "$RUNNER_TEMP/tend-agent-export"
  : > "$github_output"
  rc=0
  ACTION_PATH="$private_action" \
    TEND_HARNESS=codex \
    TEND_LIFECYCLE="$private_action/shared/steps/agent_lifecycle.py" \
    TEND_CODEX_RUNNER="$private_action/codex/runner.py" \
    TEND_SANDBOX_SETUP='' \
    TEND_CHECKOUT_MODE=base \
    TEND_BASE_BRANCH="$BASE_BRANCH" \
    TEND_BOUNDARY_PROBE_URL="http://127.0.0.1:$probe_port/" \
    TEND_BOUNDARY_PROBE_EXECUTABLE="$tool_root/probe" \
    CODEX_BIN="$codex_stub" CODEX_PROXY_URL="http://127.0.0.1:$probe_port/" \
    AUTH_MODE=api-key MODEL=stub-model EFFORT=high PROMPT='stub prompt' \
    BOT_NAME="$BOT_LOGIN" BOT_ID="$BOT_ID" \
    GITHUB_TOKEN=runner-token-must-not-cross \
    GITHUB_OUTPUT="$github_output" \
    GITHUB_STEP_SUMMARY="$runner_summary" \
    /usr/bin/python3 -E -s \
      "$TEND_TEST_ACTION_PATH/shared/steps/launch_sandbox_runtime.py" || rc=$?
  rm -rf "$private_action"
  kill "$probe_pid" 2>/dev/null || true
  wait "$probe_pid" 2>/dev/null || true
  test "$rc" -eq 0
  grep -qx 'sandbox_reaped=true' "$github_output"
  test "$(sed -n 's/^final_message=//p' "$github_output" | base64 -d)" = \
    'codex final'
  test "$(sudo -u "$SANDBOX" cat "$run_dir/tend-codex-network")" = \
    'tend-srt-network-ok'
  test "$(sudo -u "$SANDBOX" cat "$run_dir/tend-codex-local-network")" = \
    'tend-srt-local-ok'
  sudo -u "$SANDBOX" grep -qxF "HTTP_PROXY=$setup_proxy" "$codex_env"
  sudo -u "$SANDBOX" grep -qx 'TMPDIR=/home/tend-sandbox/tmp' "$codex_env"
  sudo -u "$SANDBOX" grep -qx 'NO_PROXY=' "$codex_env"
  sudo -u "$SANDBOX" grep -qx 'no_proxy=' "$codex_env"
  sudo -u "$SANDBOX" grep -q '^shell_environment_policy.set.NO_PROXY=".*127.0.0.1' \
    "$codex_argv"
  sudo -u "$SANDBOX" grep -q '^shell_environment_policy.set.no_proxy=".*127.0.0.1' \
    "$codex_argv"
  if sudo -u "$SANDBOX" grep -qE \
    'runner-token-must-not-cross|runner-service-must-not-cross' "$codex_env"; then
    echo "::error::a runner credential crossed into Codex"
    exit 1
  fi
  echo "[test-setup-sandbox] complete Claude and Codex SRT lifecycles verified"
}

verify_dispose() {
  PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/python3 -E -s \
    shared/steps/dispose_sandbox_resources.py
  test ! -e "$TEND_RUNTIME_ROOT"
  test -f "/tmp/tend-runner-owned-$GITHUB_RUN_ID"
  echo "[test-setup-sandbox] runtime container disposed, runner entries kept"
}

cleanup() {
  local shared
  shared="/opt/tend-sandbox-test-$GITHUB_RUN_ID/bin"
  if [ -n "${TEND_TEST_ACTION_PATH:-}" ]; then
    /usr/bin/sudo rm -rf -- "$TEND_TEST_ACTION_PATH"
  fi
  /usr/bin/sudo rm -f "/tmp/tend-runner-owned-$GITHUB_RUN_ID" /tmp/claude/tend-sandbox-wrote
  rmdir /tmp/claude 2>/dev/null || true
  /usr/bin/sudo rm -rf -- "${TEND_WARM_CACHE:-}" "${TEND_WARM_TREE:-}"
  rm -rf -- "$HOME/.tend-seeded" "$HOME/.cargo-install/tend-probe"
  /usr/bin/sudo rm -f /usr/local/bin/tend-probe "$shared/tend-shared" "$shared/uv"
  /usr/bin/sudo rmdir "$shared" "${shared%/bin}" 2>/dev/null || true
}

case "${1:-}" in
  plant) plant ;;
  setup) setup ;;
  install-agent-uv) install_agent_uv ;;
  verify) verify ;;
  verify-refusals) verify_refusals ;;
  verify-srt) verify_srt ;;
  verify-dispose) verify_dispose ;;
  cleanup) cleanup ;;
  *)
    echo "usage: $0 {plant|setup|install-agent-uv|verify|verify-refusals|verify-srt|verify-dispose|cleanup}" >&2
    exit 2
    ;;
esac
