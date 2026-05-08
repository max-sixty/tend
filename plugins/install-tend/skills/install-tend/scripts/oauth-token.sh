#!/usr/bin/env bash
# Obtain a long-lived Claude Code OAuth token via `claude setup-token`.
# Opens a browser for authentication, prints the access token to stdout.
#
# Requires: claude CLI
# Usage: TOKEN=$(./oauth-token.sh)
set -euo pipefail

if ! command -v claude &>/dev/null; then
  >&2 echo "Error: claude CLI not found. Install Claude Code first."
  exit 1
fi

# claude setup-token renders its TUI with Ink, which only writes to stdout
# when stdout.isTTY — so without a real TTY, the OAuth flow runs to
# completion (the localhost callback server binds, the browser approves)
# but the token is generated, displayed to nothing, and lost. We use
# script(1) to give the child a PTY so Ink renders, and capture the
# rendered output to a file.
#
# script(1) itself fails with "tcgetattr: Operation not supported on
# socket" when its own stdin is a socket (as in Claude Code's Bash tool).
# Redirecting stdin from /dev/null avoids that — the child still gets a
# real PTY for stdout.
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

>&2 echo "Running claude setup-token (approve in browser)..."
# Widen the PTY (default 80 cols) so Ink doesn't wrap the ~108-char token
# across lines. With the token on its own logical line, end-of-line is a
# natural right boundary for extraction.
#
# Redirect script(1)'s stdout passthrough to stderr (>&2): script writes
# the captured session to both the file AND its stdout, and we don't want
# that gunk leaking into our caller's `TOKEN=$(...)` capture. The user
# still sees the TUI on their terminal via stderr.
if [[ "$(uname)" == "Darwin" ]]; then
  script -q "$TMPFILE" /bin/sh -c 'stty cols 250 rows 50; exec claude setup-token' < /dev/null >&2
else
  script -qec 'stty cols 250 rows 50; exec claude setup-token' "$TMPFILE" < /dev/null >&2
fi

# Strip ANSI CSI sequences and CR. Then grep for the token: with the wide
# PTY the token sits on its own line, and grep -oE doesn't cross
# newlines, so the match can't run past the token into the next line
# ("Store this token…") — the silent "Invalid bearer token" failure mode
# that motivated this script.
TOKEN=$(sed $'s/\033\\[[^a-zA-Z]*[a-zA-Z]//g' "$TMPFILE" | tr -d '\r' \
  | grep -oE 'sk-ant-oat01-[A-Za-z0-9_-]+' | head -1)

if [ -z "$TOKEN" ]; then
  >&2 echo "Error: no sk-ant-oat01-… token found in TUI output"
  >&2 cat "$TMPFILE"
  exit 1
fi

# Sanity check: real tokens are ~108 chars.
if (( ${#TOKEN} < 80 || ${#TOKEN} > 200 )); then
  >&2 echo "Error: extracted token has implausible length (${#TOKEN} chars)"
  exit 1
fi

>&2 echo "Authentication successful."
echo "$TOKEN"
