"""Select today's area of the tracked tree for the nightly survey.

The tracked files, in path order, split into CYCLE_LENGTH runs of about equal
length, and each day takes the next run. A run in path order keeps a
directory's files together, so a night reviews one area of the tree rather
than files scattered across it. Adding or removing files moves the boundaries
between runs, so a file near one can be skipped or reviewed twice in a cycle;
the next cycle covers it.

Lock files and test snapshots are left out: they are reviewed through what
generates them, and a repository with many snapshots would otherwise spend
most of its nights on runs that hold no code.
"""

from __future__ import annotations

import subprocess
import sys
import time

CYCLE_LENGTH = 28
UNREVIEWED_SUFFIXES = (".lock", "package-lock.json", "pnpm-lock.yaml", ".snap")


def reviewable(path: str) -> bool:
    return not path.endswith(UNREVIEWED_SUFFIXES) and "snapshots/" not in path


def survey_files(files: list[str], *, unix_day: int) -> tuple[int, list[str]]:
    """Return the day's bucket and the run of files assigned to it."""
    bucket = unix_day % CYCLE_LENGTH
    ordered = sorted(path for path in files if reviewable(path))
    start = len(ordered) * bucket // CYCLE_LENGTH
    end = len(ordered) * (bucket + 1) // CYCLE_LENGTH
    return bucket, ordered[start:end]


def main(*, now: float | None = None) -> int:
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    bucket, selected = survey_files(
        tracked, unix_day=int(time.time() if now is None else now) // 86400
    )
    if selected:
        print(*selected, sep="\n")
    print(f"# bucket={bucket}/{CYCLE_LENGTH} files={len(selected)}", file=sys.stderr)
    return 0
