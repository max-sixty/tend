#!/usr/bin/env bash
# Hosted-runner integration test for the copy-on-write view, the sandbox UID and
# the PATH boundary. Commands are separate Actions steps because
# GITHUB_PATH/GITHUB_ENV affect only later steps.
#
# The properties under test are the ones no unit test can reach, because each is
# a fact about a real kernel, a real second uid and a real Actions runner:
#
#   1. The agent works in the job's own checkout and home, at their real paths,
#      and can write anywhere in them.
#   2. Nothing it writes reaches the runner. The host filesystem is byte-for-byte
#      what `setup:` left it, before the agent and after — contents included,
#      since an in-place rewrite at the same length is the copy-up an idmapped
#      lower is likeliest to get wrong.
#   3. What the view masks reads back empty: the Actions runner's own files and
#      directories, and GitHub's file-command directory. Swept for rather than
#      named, so it fails if the runner's credentials ever move.
#   4. The idmapped lower layer is what makes (1) possible: without it the same
#      overlay is EACCES for every create. `verify-view-needs-the-idmap` is that
#      negative control, run against the kernel directly.
#   5. The two environment namespaces withheld by shape, `ACTIONS_*` and
#      `INPUT_*`, are absent from the environment the harness binary received.
set -euo pipefail

# The bot identity the agent's Git config is seeded from, inside the view.
BOT_LOGIN=tend-agent
BOT_ID=4242

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
  # What a consumer's `setup:` leaves behind, at the paths it leaves it: a warm
  # cache in the runner's home, a directory owned by root (a `docker run -v`
  # artefact is the common one, and the idmap has to keep those writable), and
  # a directory to rename from the lower layer, which needs `redirect_dir=on`.
  TEND_WARM_CACHE="$HOME/.tend-warm-cache"
  mkdir -p "$TEND_WARM_CACHE/registry"
  printf 'warm-cache\n' >"$TEND_WARM_CACHE/registry/warm"
  sudo install -d -m 755 -o root -g root "$TEND_WARM_CACHE/root-owned"
  mkdir -p "$TEND_WARM_CACHE/rename-me/inner"
  # In the checkout, which on a review is the pull request's own tree.
  TEND_WARM_TREE="$GITHUB_WORKSPACE/.tend-warm-tree"
  mkdir -p "$TEND_WARM_TREE"
  printf 'built\n' >"$TEND_WARM_TREE/artifact"
  # The invariant, recorded before the agent exists. `verify-srt` re-reads it
  # after the sandbox has written all over both.
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
  # A tool `setup:` installed under the runner's home. It used to be dropped
  # from the sandbox PATH and shimmed out; it now resolves, because the home it
  # lives in is the home the agent works in.
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

# Every name, mode, owner AND content under the two directories `setup:`
# prepared. Contents because the claim is byte-for-byte: an in-place overwrite
# at the same length is exactly what overlayfs copy-up on an idmapped lower
# would get wrong, and a name-and-size digest would call it unchanged.
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
  # `sandbox_env` is applied over the job's own environment, so it wins — which
  # is why a GITHUB_* name is refused (see verify-refusals) and an ordinary one
  # is not. What this adds over the unit tests is the whole path: a config value
  # threaded through setup_sandbox.py into the file, composed by the lib,
  # landing in a real sandbox under a real uid.
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
  # The job's PATH crosses entry for entry: the runner-home directories a
  # `setup:` step installed into are now on it, because the home they are in is
  # the home the agent works in.
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

# What holds outside the view, where the sandbox uid is an ordinary "other":
# the agent's own home, and Tend's secrets sitting where it cannot reach them.
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

# `sandbox_env:` wins over the job environment, so the GitHub context is the one
# thing it may not set. This re-run exits before the proxy starts.
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

# The load-bearing claim, reproduced against the kernel rather than against
# Tend: an overlay whose lower layer is NOT idmapped presents the runner's own
# ownership to the sandbox uid, which is EACCES for every create. Everything the
# view does rests on the one `X-mount.idmap` this omits.
verify_view_needs_the_idmap() {
  local stage script rc
  stage=/var/tmp/tend-idmap-control-$GITHUB_RUN_ID
  sudo install -d -m 755 "$stage"
  script=$RUNNER_TEMP/idmap-control.sh
  cat >"$script" <<'CONTROL'
set -eu
mkdir -p "$STAGE/lower" "$STAGE/upper" "$STAGE/work" "$STAGE/view"
chmod 755 "$STAGE/view"
mount --bind -o ro "$LOWER" "$STAGE/lower"
mount -t overlay overlay \
  -o "lowerdir=$STAGE/lower,upperdir=$STAGE/upper,workdir=$STAGE/work" \
  "$STAGE/view"
exec setpriv --reuid "$SANDBOX_UID" --regid "$SANDBOX_GID" --clear-groups \
  touch "$STAGE/view/written-without-an-idmap"
CONTROL
  rc=0
  {
    sudo /usr/bin/env \
      STAGE="$stage" \
      LOWER="$TEND_WARM_CACHE" \
      SANDBOX_UID="$(id -u "$SANDBOX")" \
      SANDBOX_GID="$(id -g "$SANDBOX")" \
      /usr/bin/unshare --mount --propagation private -- \
      /bin/sh "$script"
  } >"$RUNNER_TEMP/idmap-control.log" 2>&1 || rc=$?
  cat "$RUNNER_TEMP/idmap-control.log"
  sudo rm -rf -- "$stage"
  if [ "$rc" -eq 0 ]; then
    echo "::error::a non-idmapped overlay was writable by the sandbox uid; the view's idmap is no longer the load-bearing piece and the design needs rechecking"
    exit 1
  fi
  grep -qi 'permission denied' "$RUNNER_TEMP/idmap-control.log"
  test ! -e "$TEND_WARM_CACHE/written-without-an-idmap"
  echo "[test-setup-sandbox] non-idmapped overlay refused every write, as designed"
}

verify_srt() {
  local claude_argv claude_env claude_stub codex_argv codex_env codex_stub dummy_token
  local github_output private_action probe_info probe_pid probe_port rc runner_summary
  local runner_owned setup_commands setup_proxy stream_json tool_root run_dir stub_bin
  github_output="$RUNNER_TEMP/srt-github-output"
  runner_summary="$RUNNER_TEMP/srt-step-summary"
  probe_info="$RUNNER_TEMP/srt-network-probe"
  tool_root="$TEND_TEST_ACTION_PATH/probe-bin"
  # Everything the sandbox must hand back lives here: outside the view, so it
  # survives the process tree the view dies with.
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

  # The harness stubs are planted as the RUNNER, in the runner's own home, and
  # resolve inside the view exactly as a tool a `setup:` step installed would.
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
  # What the runner keeps in its own /tmp while the agent runs. The sandbox's
  # /tmp is a tmpfs of its own, so this file is not merely unwritable inside —
  # it is not there at all, which is what the probe asserts.
  runner_owned="/tmp/tend-runner-owned-$GITHUB_RUN_ID"
  touch "$runner_owned"
  # Everything the view has to make true, asserted from inside it by the
  # consumer's own `sandbox_setup:` hook — the same place a real consumer's
  # build would hit each of these.
  setup_commands=$(printf '%s\n' \
    'set -u' \
    '# The job is the agent: same paths, same home, same PATH.' \
    'test "$PWD" = "$GITHUB_WORKSPACE"' \
    'test "$HOME" = "$TEND_RUNNER_HOME"' \
    'test "$(cat "$TEND_WARM_CACHE/registry/warm")" = warm-cache' \
    'test "$(cat "$TEND_WARM_TREE/artifact")" = built' \
    'test "$(tend-seeded)" = runner-seed' \
    'test "$TEND_FROM_SANDBOX_ENV" = applied' \
    '# Writable everywhere in it, including under a root-owned directory the' \
    '# idmap has to keep well-defined, and across a lower-layer rename.' \
    'printf "agent\n" > "$TEND_WARM_CACHE/registry/written-by-sandbox"' \
    '# In place, at the same length: the copy-up path an idmapped lower is' \
    '# most likely to get wrong, and the one a digest of names would miss.' \
    'printf "AGENT-CACHE\n" > "$TEND_WARM_CACHE/registry/warm"' \
    'printf "agent\n" > "$TEND_WARM_TREE/written-by-sandbox"' \
    'printf "agent\n" > "$TEND_WARM_CACHE/root-owned/written-by-sandbox"' \
    'mv "$TEND_WARM_CACHE/rename-me" "$TEND_WARM_CACHE/renamed"' \
    'ln "$TEND_WARM_CACHE/registry/warm" "$TEND_WARM_TREE/linked"' \
    'printf "sandbox\n" > "$GITHUB_WORKSPACE/.tend-srt-wrote-here"' \
    '# The runner-side halves of Tend, and the runner itself, stay out of reach.' \
    'if [ -r "$TEND_PRIVATE_DIR/tend-proxy/mitmproxy-ca.pem" ]; then exit 90; fi' \
    '# The direct negative: the runner service identity reads empty wherever' \
    '# it lives. A sweep rather than a path, so it also catches GitHub moving' \
    '# those files somewhere the derived mask would not follow.' \
    'if find "$TEND_RUNNER_HOME" -maxdepth 4 -size +0 -name ".credentials*" -print -o -maxdepth 4 -size +0 -name ".runner" -print 2>/dev/null | grep -q .; then exit 94; fi' \
    'if ls "$TEND_RUNTIME_ROOT/view" >/dev/null 2>&1; then exit 93; fi' \
    'test -z "${GITHUB_ENV:-}"' \
    'test -z "${ACTIONS_RUNTIME_TOKEN:-}"' \
    '# Scratch: /tmp is a private tmpfs; /var/tmp belongs to the runner.' \
    'touch /tmp/tend-sandbox-scratch' \
    'if touch /var/tmp/tend-unscoped 2>/dev/null; then exit 91; fi' \
    "if [ -e '$runner_owned' ]; then exit 92; fi" \
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
    TEND_BASE_BRANCH="${GITHUB_REF_NAME:-main}" \
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
    GITHUB_OUTPUT="$github_output" \
    GITHUB_STEP_SUMMARY="$runner_summary" \
    /usr/bin/python3 -E -s \
      "$TEND_TEST_ACTION_PATH/shared/steps/launch_sandbox_runtime.py" || rc=$?
  test "$rc" -eq 0
  grep -qx 'sandbox_reaped=true' "$github_output"
  stream_json=$(sed -n 's/^stream_json=//p' "$github_output")
  test -n "$stream_json"
  grep -q '"stub turn"' "$stream_json"
  grep -q 'stub turn' "$runner_summary"

  # (1) The view presented the runner's home as the sandbox's own, which is the
  # one number that settles the idmap's direction.
  test "$(sudo -u "$SANDBOX" cat "$run_dir/tend-view-owner")" = \
    "$(id -u "$SANDBOX"):$(id -g "$SANDBOX")"
  # (2) Nothing the sandbox wrote reached the runner — not the files, not the
  # rename, not the hard link, not even the mode of a directory it wrote under.
  test "$(host_checksum)" = "$TEND_HOST_SUM"
  test "$(cat "$TEND_WARM_CACHE/registry/warm")" = warm-cache
  test ! -e "$GITHUB_WORKSPACE/.tend-srt-wrote-here"
  test ! -e "$TEND_WARM_CACHE/registry/written-by-sandbox"
  test ! -e "$TEND_WARM_CACHE/root-owned/written-by-sandbox"
  test ! -e "$TEND_WARM_TREE/written-by-sandbox"
  test -d "$TEND_WARM_CACHE/rename-me"
  test ! -e "$TEND_WARM_CACHE/renamed"
  # The event checkout ran inside the view too, so the runner's own HEAD is
  # exactly what the workflow checked out.
  test "$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)" = "$TEND_HOST_HEAD"

  # The agent commits without configuring an identity of its own, including
  # from a clone it makes itself, and that identity is now set inside the view.
  test "$(sudo -u "$SANDBOX" cat "$run_dir/tend-git-identity")" = \
    "${BOT_ID}+${BOT_LOGIN}@users.noreply.github.com"
  setup_proxy=$(sudo -u "$SANDBOX" cat "$run_dir/tend-setup-proxy")
  test -n "$setup_proxy"
  test "$setup_proxy" != 'http://127.0.0.1:8899'
  sudo -u "$SANDBOX" grep -qxF "HTTP_PROXY=$setup_proxy" "$claude_env"
  sudo -u "$SANDBOX" grep -qx 'TMPDIR=/home/tend-sandbox/tmp' "$claude_env"
  sudo -u "$SANDBOX" grep -qxF "HOME=$HOME" "$claude_env"
  sudo -u "$SANDBOX" test -f /home/tend-sandbox/tmp/tend-scratch-probe
  # The sandbox wrote /tmp/tend-sandbox-scratch and the write succeeded; it
  # landed in the tmpfs SRT mounts over /tmp, which went with the process tree.
  test ! -e /tmp/tend-sandbox-scratch
  sudo -u "$SANDBOX" grep -qxF "GITHUB_TOKEN=$dummy_token" "$claude_env"
  if sudo -u "$SANDBOX" grep -q '^GITHUB_ENV=' "$claude_env"; then
    echo "::error::runner command-file path crossed into Claude"
    exit 1
  fi
  # The two namespaces withheld by shape, in the environment the harness binary
  # actually received: `ACTIONS_*` is the runner's service channel — its
  # RUNTIME_TOKEN is write access to the Actions cache, the one path a sandbox
  # write could take into a later run — and `INPUT_*` is how an action's
  # inputs, some of them this job's real secrets, reach a step.
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
    TEND_BASE_BRANCH="${GITHUB_REF_NAME:-main}" \
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

# The real dispose step, against the real filesystem: the runtime container
# goes, and with it the view's upper layer and Tend's own secrets. The
# runner-owned /tmp file is asserted again here as a regression guard — the
# sandbox never saw it, so only a step that went looking through /tmp again
# could remove it.
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
  /usr/bin/sudo rm -f "/tmp/tend-runner-owned-$GITHUB_RUN_ID"
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
  verify-view-needs-the-idmap) verify_view_needs_the_idmap ;;
  verify-srt) verify_srt ;;
  verify-dispose) verify_dispose ;;
  cleanup) cleanup ;;
  *)
    echo "usage: $0 {plant|setup|install-agent-uv|verify|verify-refusals|verify-view-needs-the-idmap|verify-srt|verify-dispose|cleanup}" >&2
    exit 2
    ;;
esac
