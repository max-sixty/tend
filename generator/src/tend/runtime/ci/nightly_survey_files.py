"""Select today's deterministic slice of tracked files for the nightly survey."""

from __future__ import annotations

import subprocess
import sys
import time
import zlib

CYCLE_LENGTH = 28


def survey_files(files: list[str], *, unix_day: int) -> tuple[int, list[str]]:
    """Return the day's bucket and the files assigned to it."""
    bucket = unix_day % CYCLE_LENGTH
    selected = [
        path for path in files if zlib.crc32(path.encode()) % CYCLE_LENGTH == bucket
    ]
    return bucket, selected


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
