# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Move the sandbox boundary's pins onto a fresh Ubuntu archive snapshot.

`shared/steps/install-sandbox-runtime.sh` resolves bubblewrap, socat, ripgrep
and SRT's npm tree as of one recorded instant rather than from whatever the
live sources hold. Deriving the next instant's Debian versions by hand means
reading three pockets and comparing Debian version strings, so the weekly sweep
runs this instead: it rewrites the four constants in place and prints what
moved. Review `git diff`, and let `test-sandbox` decide whether the new
bubblewrap still builds a sandbox Tend can launch into.

Versions come from the snapshot rather than the live archive because the two
differ by hours, and it is the snapshot the runners install from. `dpkg
--compare-versions` picks between pockets, since apt takes the highest version
across them rather than preferring one.
"""

from __future__ import annotations

import argparse
import lzma
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_PATH = Path("shared/steps/install-sandbox-runtime.sh")
SNAPSHOT_HOST = "https://snapshot.ubuntu.com/ubuntu"
SERIES = "noble"
POCKETS = (SERIES, f"{SERIES}-updates", f"{SERIES}-security")
# `main` carries bubblewrap and socat, `universe` ripgrep. Both are listed in
# the sources the install script writes, so both are read here.
COMPONENTS = ("main", "universe")
ARCH = "amd64"
# Constant name in the shell script -> binary package it pins.
PACKAGES = {
    "BUBBLEWRAP_VERSION": "bubblewrap",
    "SOCAT_VERSION": "socat",
    "RIPGREP_VERSION": "ripgrep",
}


def index_url(instant: str, pocket: str, component: str) -> str:
    snapshot = instant.replace(":", "").replace("-", "")
    return f"{SNAPSHOT_HOST}/{snapshot}/dists/{pocket}/{component}/binary-{ARCH}/Packages.xz"


def published_versions(url: str, wanted: set[str]) -> dict[str, str]:
    """Binary package versions in one Packages index, for `wanted` only."""
    with urllib.request.urlopen(url) as response:  # noqa: S310 — fixed https host
        index = lzma.decompress(response.read()).decode()

    found: dict[str, str] = {}
    for stanza in index.split("\n\n"):
        name = re.search(r"^Package: (\S+)$", stanza, re.MULTILINE)
        version = re.search(r"^Version: (\S+)$", stanza, re.MULTILINE)
        if name and version and name[1] in wanted:
            found[name[1]] = version[1]
    return found


def newer(candidate: str, incumbent: str | None) -> bool:
    if incumbent is None:
        return True
    return (
        subprocess.run(
            ["dpkg", "--compare-versions", candidate, "gt", incumbent], check=False
        ).returncode
        == 0
    )


def resolve(instant: str) -> dict[str, str]:
    """The version apt would install from the snapshot, per package."""
    wanted = set(PACKAGES.values())
    highest: dict[str, str] = {}
    for pocket in POCKETS:
        for component in COMPONENTS:
            for name, version in published_versions(
                index_url(instant, pocket, component), wanted
            ).items():
                if newer(version, highest.get(name)):
                    highest[name] = version

    missing = sorted(wanted - highest.keys())
    if missing:
        sys.exit(f"{instant} publishes no {', '.join(missing)} for {SERIES}/{ARCH}")
    return highest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instant",
        default=datetime.now(UTC).strftime("%Y-%m-%dT%H:00:00Z"),
        help="ISO-8601 Zulu instant to resolve against (default: this hour)",
    )
    instant = parser.parse_args().instant

    script = SCRIPT_PATH.read_text()
    resolved = resolve(instant)
    moves = {"PACKAGES_RESOLVED_AT": instant} | {
        constant: resolved[package] for constant, package in PACKAGES.items()
    }

    changed = False
    for constant, value in moves.items():
        pattern = rf"^{constant}=(\S+)$"
        current = re.search(pattern, script, re.MULTILINE)
        if current is None:
            sys.exit(f"{SCRIPT_PATH} no longer sets {constant}")
        if current[1] == value:
            print(f"{constant} {value} (unchanged)")
            continue
        print(f"{constant} {current[1]} -> {value}")
        script = re.sub(pattern, f"{constant}={value}", script, flags=re.MULTILINE)
        changed = True

    if changed:
        SCRIPT_PATH.write_text(script)


if __name__ == "__main__":
    main()
