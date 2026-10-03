#!/usr/bin/env bash
# Install pinned uv into a caller-owned directory without changing PATH.
# The release archive is checked against a sha256 committed here, so nothing
# fetched at job time — an installer script, a re-uploaded asset — decides what
# runs. `UV_SHA256` in generator/src/tend/workflows.py carries the same pair.
# Inputs (env): UV_INSTALL_DIR.
set -euo pipefail

UV_VERSION=0.12.22
case "$(uname -m)" in
  x86_64)
    target=x86_64-unknown-linux-gnu
    sha256=b9980552309f09c15172b8be828555e375097f16deb459795ce7bfd200380f0b
    ;;
  aarch64)
    target=aarch64-unknown-linux-gnu
    sha256=6f66a14e8239871fb477f9746c941fedfa77e8fe28a8bc7c07e1dc7f53a66712
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
