#!/usr/bin/env bash
# Install pinned uv into a caller-owned directory without changing PATH.
# The release archive is checked against a sha256 committed here, so nothing
# fetched at job time — an installer script, a re-uploaded asset — decides what
# runs.
# Inputs (env): UV_INSTALL_DIR.
set -euo pipefail

UV_VERSION=0.13.8
case "$(uname -m)" in
  x86_64)
    target=x86_64-unknown-linux-gnu
    sha256=5acfd9177cfecf0bf87578f059728215ccc884388988f55f7a65041b57387d91
    ;;
  aarch64)
    target=aarch64-unknown-linux-gnu
    sha256=74f2f5c6024d397481db228db8fc25eed420db192243f88438c118e7e013e585
    ;;
  *)
    echo "::error::no pinned uv archive for $(uname -m)" >&2
    exit 1
    ;;
esac

mkdir -p "$UV_INSTALL_DIR"
archive="${UV_INSTALL_DIR}/uv-${target}.tar.gz"
# --retry rides out the transient 5xx and timeouts a release download hits.
# setup-runtime runs this before the harness action, whose failure report a
# failed step skips, so an unretried blip here loses the run unrecorded.
curl -fsSL --retry 5 -o "$archive" \
  "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${target}.tar.gz"
echo "${sha256}  ${archive}" | sha256sum --check --quiet
tar -xzf "$archive" -C "$UV_INSTALL_DIR" --strip-components=1 --no-same-owner
rm "$archive"

"${UV_INSTALL_DIR}/uv" --version
"${UV_INSTALL_DIR}/uvx" --version
