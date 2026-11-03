"""Validate built distributions and reject Tend's placeholder version."""

from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> None:
    artifacts = sorted(
        path for pattern in ("*.whl", "*.tar.gz") for path in Path("dist").glob(pattern)
    )
    subprocess.run(
        [
            "uv",
            "tool",
            "run",
            "--from",
            "twine",
            "twine",
            "check",
            *map(str, artifacts),
        ],
        check=True,
    )
    if Path("dist/tend-0.0.0.tar.gz").is_file():
        raise SystemExit("Invalid version number")


if __name__ == "__main__":
    main()
