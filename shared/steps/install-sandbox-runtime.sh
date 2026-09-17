#!/usr/bin/bash
set -euo pipefail

: "${SRT_VERSION:?SRT_VERSION is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${RUNNER_TOOL_CACHE:?RUNNER_TOOL_CACHE is required}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"

# Both installs below reach outside the repository for code that runs at the
# sandbox boundary, and both used to take whatever upstream published that
# morning. On 2026-09-17 a bubblewrap security update changed how bwrap
# resolves a bind destination, and every consumer's sessions failed at launch
# with no commit here to blame. The npm tree carries the same exposure one
# level down: SRT's `zod`, `commander`, `node-forge` and
# `@pondwader/socks5-server` ranges float, and that tree builds the bwrap argv
# and terminates the agent's TLS.
#
# So both resolve as of one recorded instant rather than from a live mirror:
# snapshot.ubuntu.com serves each Ubuntu pocket at a timestamp, signed by the
# archive keys already in the runner's keyring, and npm's `--before` resolves a
# whole tree as of a date. The Debian versions below are what that instant
# yields; naming them makes a bump say what moved, and stops a runner image
# that ships its own bubblewrap from quietly supplying a different sandbox.
#
# The cost is that an upstream security fix waits for a commit here.
# `running-tend`'s weekly sweep is that commit, so a bubblewrap fix reaches
# consumers within the week, behind a green `test-sandbox` rather than ahead
# of it.
PACKAGES_RESOLVED_AT=2026-09-17T18:00:00Z
BUBBLEWRAP_VERSION=0.9.0-1ubuntu0.2
SOCAT_VERSION=1.8.0.0-4ubuntu0.1
RIPGREP_VERSION=14.1.0-1

apparmor_userns=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
if [ -r "$apparmor_userns" ] && [ "$(/usr/bin/cat "$apparmor_userns")" = 1 ]; then
  if [ "${TEND_RUNNER_ENVIRONMENT:-}" = github-hosted ]; then
    # Record rollback before changing host state so cancellation or a failed
    # sysctl cannot leave an untracked policy change.
    echo "TEND_RESTORE_APPARMOR_USERNS=true" >> "$GITHUB_ENV"
    /usr/bin/sudo /usr/sbin/sysctl -q -w kernel.apparmor_restrict_unprivileged_userns=0
  else
    echo "::notice::Leaving self-hosted AppArmor policy unchanged; without a bwrap AppArmor profile granting unprivileged user namespaces, SRT fails as 'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted' — install such a profile or set kernel.apparmor_restrict_unprivileged_userns=0 on the runner"
  fi
fi

case "$(/usr/bin/uname -m)" in
  x86_64) srt_arch=x64 ;;
  aarch64) srt_arch=arm64 ;;
  *) echo "::error::SRT is unsupported on $(/usr/bin/uname -m)"; exit 1 ;;
esac

node_bin=$(/usr/bin/find "$RUNNER_TOOL_CACHE/node" -path "*/$srt_arch/bin/node" -type f -print \
  | /usr/bin/sort -V | /usr/bin/tail -n1)
if [ -z "$node_bin" ] || [ ! -x "$node_bin" ]; then
  echo "::error::No trusted x64 Node runtime found in RUNNER_TOOL_CACHE"
  exit 1
fi
npm_cli="${node_bin%/bin/node}/lib/node_modules/npm/bin/npm-cli.js"
if [ ! -f "$npm_cli" ]; then
  echo "::error::Trusted Node runtime has no npm sibling"
  exit 1
fi

# Compare against what dpkg has rather than what is on PATH: a runner carrying
# some other bubblewrap satisfies a presence check and still launches a
# different sandbox. A runner already holding all three exactly skips apt, and
# with it sudo, which is the one thing a self-hosted runner may not grant.
stale=()
ahead=()
for spec in \
  "bubblewrap=$BUBBLEWRAP_VERSION" \
  "socat=$SOCAT_VERSION" \
  "ripgrep=$RIPGREP_VERSION"
do
  status=$(/usr/bin/dpkg-query -W -f='${db:Status-Status} ${Version}' "${spec%%=*}" 2>/dev/null || true)
  [ "$status" = "installed ${spec#*=}" ] && continue
  stale+=("$spec")
  case "$status" in
    "installed "*)
      if /usr/bin/dpkg --compare-versions "${status#installed }" gt "${spec#*=}"; then
        ahead+=("${spec%%=*} ${status#installed }")
      fi
      ;;
  esac
done

if [ "${#stale[@]}" -gt 0 ]; then
  # `;` not `&&`: a host with no os-release should reach the message below
  # rather than die on the `.` under `set -e`.
  codename=$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}")
  if [ "$codename" != noble ]; then
    echo "::error::The pinned sandbox capabilities are Ubuntu noble builds and this runner is '${codename:-unknown}'. Run Tend on ubuntu-24.04, or preinstall bubblewrap $BUBBLEWRAP_VERSION, socat $SOCAT_VERSION and ripgrep $RIPGREP_VERSION."
    exit 1
  fi

  # Series first, since a host off noble reads as ahead of the pin only
  # because the pin is a noble build — no bump here ever reaches it.
  #
  # A GitHub-hosted runner is disposable, so rolling it back onto the pin costs
  # its owner nothing. A self-hosted runner is someone's machine, where
  # replacing a security update they have already taken would outlive the job —
  # the restraint the AppArmor branch above applies to host policy.
  if [ "${#ahead[@]}" -gt 0 ] && [ "${TEND_RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
    echo "::error::This runner is ahead of Tend's pin (${ahead[*]}) and Tend does not downgrade a self-hosted host. Wait for Tend's weekly bump to reach those versions, or run Tend on a runner holding bubblewrap $BUBBLEWRAP_VERSION, socat $SOCAT_VERSION and ripgrep $RIPGREP_VERSION."
    exit 1
  fi

  # Scoped to this apt invocation with -o rather than written into /etc, so a
  # self-hosted runner keeps both its own sources and its own fetched lists.
  apt_state=$(/usr/bin/mktemp -d /tmp/tend-apt.XXXXXX)
  # The lists below end up owned by `_apt`, so the runner user cannot unlink
  # them afterwards. Clear the tree from the failure paths too, or a
  # self-hosted host collects a root-owned index tree per failed run.
  trap '/usr/bin/sudo /usr/bin/rm -rf "${apt_state:?}"' EXIT
  /usr/bin/mkdir -p "$apt_state/lists/partial" "$apt_state/parts"
  # snapshot.ubuntu.com spells the same instant without the separators.
  snapshot="https://snapshot.ubuntu.com/ubuntu/${PACKAGES_RESOLVED_AT//[:-]/}"
  for suite in noble noble-updates noble-security; do
    echo "deb $snapshot $suite main universe"
  done > "$apt_state/sources.list"
  apt_options=(
    -o "Dir::Etc::SourceList=$apt_state/sources.list"
    -o "Dir::Etc::SourceParts=$apt_state/parts"
    -o "Dir::State::Lists=$apt_state/lists"
    -o Acquire::Languages=none
  )
  # apt drops to `_apt` for the downloads, and falls back to fetching as root —
  # one warning per index — if that user cannot reach them. It needs to write
  # the lists and to cross mktemp's 0700 parent, so the parent gets search and
  # not read: unlike a bwrap bind destination, apt never lists this directory.
  /usr/bin/chmod 711 "$apt_state"
  /usr/bin/sudo /usr/bin/chown -R _apt "$apt_state/lists"
  /usr/bin/sudo /usr/bin/apt-get "${apt_options[@]}" update
  # The snapshot holds one candidate per pocket like any other mirror, so a
  # runner image shipping something newer needs the downgrade spelled out.
  /usr/bin/sudo /usr/bin/apt-get "${apt_options[@]}" install -y --allow-downgrades "${stale[@]}"
fi

runtime_root=$(/usr/bin/mktemp -d /tmp/tend-runtime.XXXXXX)
# Publish the cleanup target before any fallible install work. The later
# always() cleanup can then remove a partial runtime too.
echo "TEND_RUNTIME_ROOT=$runtime_root" >> "$GITHUB_ENV"
srt_root="$runtime_root/srt"
npm_userconfig=$(/usr/bin/mktemp "$RUNNER_TEMP/tend-npm-user.XXXXXX")
npm_globalconfig=$(/usr/bin/mktemp "$RUNNER_TEMP/tend-npm-global.XXXXXX")
/usr/bin/env -i \
  PATH="${node_bin%/node}:/usr/sbin:/usr/bin:/sbin:/bin" HOME="$RUNNER_TEMP" \
  "$node_bin" "$npm_cli" install --prefix "$srt_root" \
    --userconfig "$npm_userconfig" --globalconfig "$npm_globalconfig" \
    --ignore-scripts --no-audit --no-fund \
    --before "$PACKAGES_RESOLVED_AT" \
    "@anthropic-ai/sandbox-runtime@$SRT_VERSION"
srt_package="$srt_root/node_modules/@anthropic-ai/sandbox-runtime"
srt_entry="$srt_package/dist/index.js"
srt_seccomp="$srt_package/vendor/seccomp/$srt_arch/apply-seccomp"
for command in "$srt_entry" "$srt_seccomp" /usr/bin/bwrap /usr/bin/socat /usr/bin/rg; do
  [ -e "$command" ] || { echo "::error::Missing SRT capability: $command"; exit 1; }
done
/usr/bin/chmod 755 "$runtime_root"
{
  echo "NODE_BIN=$node_bin"
  echo "TEND_NPM_CLI=$npm_cli"
  echo "TEND_NPM_USERCONFIG=$npm_userconfig"
  echo "TEND_NPM_GLOBALCONFIG=$npm_globalconfig"
  echo "TEND_SRT_ENTRY=$srt_entry"
  echo "TEND_SRT_SECCOMP=$srt_seccomp"
} >> "$GITHUB_ENV"
