# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Delete what the sandbox ran from and what it left behind, after the reap.

The event checkout and the per-run runtime are named containers under
``/var/tmp``; ``/tmp`` is scratch the sandbox shares with the runner, so what
the sandbox uid owns at the top level of ``/tmp`` goes too, before a later
runner step or a ``setup:`` action's POST step reads it.
"""

from __future__ import annotations

import os
import pwd
import re
import subprocess
from pathlib import Path

CONTAINER_PARENT = Path("/var/tmp")
SANDBOX_SCRATCH = Path("/tmp")
WORKSPACE_CONTAINER = re.compile(r"tend-agent-workspace-[A-Za-z0-9._-]+\Z")
RUNTIME_CONTAINER = re.compile(r"tend-runtime\.[A-Za-z0-9]+\Z")


def fail(message: str) -> int:
    print(f"::error::{message}", flush=True)
    return 1


def workspace_container(workspace: Path) -> Path:
    """Return the dedicated /var/tmp container or reject a broader target."""
    if workspace.name != "checkout":
        raise ValueError("TEND_AGENT_WORKSPACE must end in /checkout")
    container = workspace.parent
    if container.parent != CONTAINER_PARENT or not WORKSPACE_CONTAINER.fullmatch(
        container.name
    ):
        raise ValueError(
            "TEND_AGENT_WORKSPACE must be inside a "
            "tend-agent-workspace-* /var/tmp container"
        )
    return container


def runtime_container(runtime: Path) -> Path:
    """Accept only the mktemp shape used by install-sandbox-runtime.sh."""
    if runtime.parent != CONTAINER_PARENT or not RUNTIME_CONTAINER.fullmatch(
        runtime.name
    ):
        raise ValueError(
            "TEND_RUNTIME_ROOT must be a tend-runtime.* /var/tmp directory"
        )
    return runtime


def scratch_entries(sandbox: str) -> list[Path]:
    """Return the top-level /tmp entries the sandbox uid owns.

    Ownership is the whole test: /tmp is sticky, so an entry the sandbox uid
    owns is one the sandbox created, and a runner-owned entry beside it — a
    `setup:` action's state, whatever the runner image keeps here — stays.
    """
    uid = pwd.getpwnam(sandbox).pw_uid
    owned = []
    for entry in sorted(SANDBOX_SCRATCH.iterdir()):
        try:
            owner = entry.lstat().st_uid
        except FileNotFoundError:
            # Someone else's temp file, removed between the listing and the
            # stat. Already gone is what this step wants; /tmp is shared, so
            # reading it races anything else still running on the runner.
            continue
        if owner == uid:
            owned.append(entry)
    return owned


def targets() -> list[Path]:
    """Resolve every resource that this run got far enough to create."""
    resources: list[Path] = []
    workspace = os.environ.get("TEND_AGENT_WORKSPACE", "")
    if workspace:
        resources.append(workspace_container(Path(workspace)))
    runtime = os.environ.get("TEND_RUNTIME_ROOT", "")
    if runtime:
        resources.append(runtime_container(Path(runtime)))
    return resources


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
            resources.extend(scratch_entries(sandbox))
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
