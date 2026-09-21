# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Delete the per-run runtime container, and with it the view's upper layer."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

CONTAINER_PARENT = Path("/var/tmp")
RUNTIME_CONTAINER = re.compile(r"tend-runtime\.[A-Za-z0-9]+\Z")


def fail(message: str) -> int:
    print(f"::error::{message}", flush=True)
    return 1


def runtime_container(runtime: Path) -> Path:
    """Accept only the mktemp shape used by install-sandbox-runtime.sh."""
    if runtime.parent != CONTAINER_PARENT or not RUNTIME_CONTAINER.fullmatch(
        runtime.name
    ):
        raise ValueError(
            "TEND_RUNTIME_ROOT must be a tend-runtime.* /var/tmp directory"
        )
    return runtime


def targets() -> list[Path]:
    """Resolve every resource that this run got far enough to create."""
    runtime = os.environ.get("TEND_RUNTIME_ROOT", "")
    return [runtime_container(Path(runtime))] if runtime else []


def main() -> int:
    try:
        resources = targets()
        sandbox = os.environ.get("SANDBOX", "")
        if sandbox:
            live = subprocess.run(
                ["/usr/bin/pgrep", "-u", sandbox],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if live.returncode == 0:
                return fail(
                    "refusing to dispose resources while sandbox processes live"
                )
            if live.returncode != 1:
                return fail("could not verify that sandbox processes were reaped")
        if not resources:
            return 0
        subprocess.run(
            [
                "/usr/bin/sudo",
                "/usr/bin/rm",
                "-rf",
                "--",
                *(str(resource) for resource in resources),
            ],
            check=True,
        )
        remaining = [
            str(resource) for resource in resources if os.path.lexists(resource)
        ]
        if remaining:
            return fail(
                f"sandbox resources still exist after cleanup: {', '.join(remaining)}"
            )
        return 0
    except (OSError, subprocess.CalledProcessError, ValueError) as problem:
        return fail(str(problem))


if __name__ == "__main__":
    raise SystemExit(main())
