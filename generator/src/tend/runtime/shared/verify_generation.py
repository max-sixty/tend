"""Verify an install PR using the generator installed by its pinned action.

The checkout may contain only the PR head. Fetch the repository's default
branch before regeneration so local default-branch detection stays correct.
Compare against HEAD after staging intents: plain worktree diffs miss newly
emitted files and deletions staged by git add.
"""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from tend.runtime.shared import _common

GENERATED_PATHS = (".github", ".config", "CODEOWNERS", "docs/CODEOWNERS")


def prepare_default_branch() -> None:
    repo = _common.require_env("GITHUB_REPOSITORY")["GITHUB_REPOSITORY"]
    branch = _common.gh_json("api", f"repos/{repo}")["default_branch"]
    remote = f"refs/remotes/origin/{branch}"
    subprocess.run(
        ["git", "fetch", "origin", "--depth", "1", f"+refs/heads/{branch}:{remote}"],
        check=True,
    )
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", remote], check=True
    )


def check_drift() -> int:
    subprocess.run(["git", "add", "--intent-to-add", *GENERATED_PATHS[:2]], check=True)
    for name in GENERATED_PATHS[2:]:
        path = Path(name)
        if path.exists() or path.is_symlink():
            subprocess.run(["git", "add", "--intent-to-add", name], check=True)
    result = subprocess.run(
        ["git", "diff", "HEAD", "--quiet", "--", *GENERATED_PATHS], check=False
    )
    if result.returncode:
        _common.fail(
            "Committed files differ from "
            f"'uvx tend@{version('tend')} init --with-install-test' output. "
            "Run it locally and commit the result."
        )
        subprocess.run(
            ["git", "--no-pager", "diff", "HEAD", "--", *GENERATED_PATHS], check=True
        )
        return 1
    return 0


def main() -> int:
    prepare_default_branch()
    subprocess.run(
        [sys.executable, "-I", "-m", "tend", "init", "--with-install-test"], check=True
    )
    return check_drift()
