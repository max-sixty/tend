"""Publish the tag's exact CHANGELOG section as its GitHub release notes."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def release_notes(changelog: str, version: str) -> str:
    lines = changelog.splitlines(keepends=True)
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if line.rstrip("\r\n") == f"## {version}"
        ),
        None,
    )
    if start is None:
        return ""
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "".join(lines[start:end])


def main() -> None:
    version = os.environ["GITHUB_REF_NAME"]
    notes = release_notes(Path("CHANGELOG.md").read_text(), version)
    path = Path(os.environ["RUNNER_TEMP"]) / "notes.md"
    path.write_text(notes)
    if not notes:
        raise SystemExit(
            f"::error::No CHANGELOG.md section for {version} in the tagged commit. "
            "The tag is immutable, so re-running cannot fix this — add the section "
            f"on main and run 'gh release create {version}' manually with the extracted notes."
        )
    subprocess.run(
        [
            "gh",
            "release",
            "create",
            version,
            "--repo",
            os.environ["GITHUB_REPOSITORY"],
            "--title",
            version,
            "--notes-file",
            str(path),
            "--verify-tag",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
