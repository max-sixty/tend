#!/usr/bin/env bash
# Install pinned uv into a caller-owned directory without changing PATH.
# The release archive is checked against a sha256 committed here, so nothing
# fetched at job time — an installer script, a re-uploaded asset — decides what
# runs. `UV_SHA256` in generator/src/tend/workflows.py carries the same pair.
# Inputs (env): UV_INSTALL_DIR.
set -euo pipefail

UV_VERSION=0.12.23
case "$(uname -m)" in
  x86_64)
    target=x86_64-unknown-linux-gnu
    sha256=9167d72b3319674b6303c4cbe071854bba13ebdf3d76b1a7cbdc175471fb66d6
    ;;
  aarch64)
    target=aarch64-unknown-linux-gnu
    sha256=6524bd338177ed50d035d39354e12545e993bbeba2ecbddf0480c5b3a81d313f
    ;;
  *)
    echo "::error::no pinned uv archive for $(uname -m)" >&2
    exit 1
    ;;
esac

mkdir -p "$UV_INSTALL_DIR"
archive="${UV_INSTALL_DIR}/uv-${target}.tar.gz"
curl -fsSL -o "$archive" \
  "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${target}.tar.gz"
echo "${sha256}  ${archive}" | sha256sum --check --quiet
tar -xzf "$archive" -C "$UV_INSTALL_DIR" --strip-components=1 --no-same-owner
rm "$archive"

"${UV_INSTALL_DIR}/uv" --version
"${UV_INSTALL_DIR}/uvx" --version
