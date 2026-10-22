#!/usr/bin/env bash
# Install pinned uv into a caller-owned directory without changing PATH.
# The release archive is checked against a sha256 committed here, so nothing
# fetched at job time — an installer script, a re-uploaded asset — decides what
# runs. `UV_SHA256` in generator/src/tend/workflows.py carries the same pair.
# Inputs (env): UV_INSTALL_DIR.
set -euo pipefail

UV_VERSION=0.13.5
case "$(uname -m)" in
  x86_64)
    target=x86_64-unknown-linux-gnu
    sha256=f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f
    ;;
  aarch64)
    target=aarch64-unknown-linux-gnu
    sha256=4bb69cf95ed13f23713bb7bc09f241896ea0c75b7c557e615a20604b82f9c403
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
