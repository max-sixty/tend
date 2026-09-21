#!/usr/bin/bash
set -euo pipefail

: "${GITHUB_ENV:?GITHUB_ENV is required}"

# The per-run container the dispose step deletes: the staged lifecycle, Tend's
# runner-side secrets, and the view's upper layer. /var/tmp, because the
# sandbox's /tmp is a tmpfs of its own.
runtime_root=$(/usr/bin/mktemp -d /var/tmp/tend-runtime.XXXXXX)
echo "TEND_RUNTIME_ROOT=$runtime_root" >> "$GITHUB_ENV"
# Tend's runner-side secrets, outside the home the agent sees through the view.
private_dir="$runtime_root/private"
/usr/bin/mkdir -m 700 "$private_dir"
echo "TEND_PRIVATE_DIR=$private_dir" >> "$GITHUB_ENV"
/usr/bin/chmod 755 "$runtime_root"
