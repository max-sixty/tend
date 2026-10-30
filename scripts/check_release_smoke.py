"""Require successful manually dispatched Codex smoke on the release commit."""

from __future__ import annotations

import json
import os
import subprocess


def main() -> None:
    sha = os.environ["GITHUB_SHA"]
    runs = json.loads(
        subprocess.check_output(
            [
                "gh",
                "run",
                "list",
                "--repo",
                os.environ["GITHUB_REPOSITORY"],
                "--workflow",
                "codex-model-smoke.yaml",
                "--branch",
                "main",
                "--commit",
                sha,
                "--event",
                "workflow_dispatch",
                "--json",
                "headSha,status,conclusion",
            ],
            text=True,
        )
    )
    if not any(
        run["headSha"] == sha
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        for run in runs
    ):
        raise SystemExit("No successful Codex smoke for the release commit")


if __name__ == "__main__":
    main()
