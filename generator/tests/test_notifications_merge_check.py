"""Conflict admission uses current fetched refs, even if API OIDs are stale."""

import subprocess
from pathlib import Path

from tend.runtime.shared import notifications_check


def test_conflict_gate_rechecks_base_and_head(tmp_path: Path, monkeypatch) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(remote), *args], text=True
        ).strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (remote / "file").write_text("original\n")
    git("add", ".")
    git("commit", "-m", "initial")
    git("checkout", "-b", "topic")
    (remote / "file").write_text("topic\n")
    git("commit", "-am", "topic")
    git("update-ref", "refs/pull/1/head", "HEAD")
    git("checkout", "main")

    pr = {
        "number": 1,
        "baseRefName": "main",
        "mergeable": "CONFLICTING",
        "headRefOid": "stale-head",
        "comments": {"nodes": []},
    }
    monkeypatch.setattr(notifications_check._common, "gh", lambda *a, **k: "bot")
    monkeypatch.setattr(
        notifications_check._common,
        "gh_json",
        lambda *a, **k: {"data": {"search": {"nodes": [pr]}}},
    )
    run = subprocess.run

    def local_run(args, **kwargs):
        args = [
            str(remote) if a == "https://github.com/owner/repo.git" else a for a in args
        ]
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", local_run)
    assert notifications_check._conflicts("owner/repo") == 0

    # The head is unchanged; moving the base must restore admission.
    (remote / "file").write_text("base\n")
    git("commit", "-am", "base conflicts")
    assert notifications_check._conflicts("owner/repo") == 1

    # Moving the PR head back to the new base makes the merge clean again.
    git("update-ref", "refs/pull/1/head", "HEAD")
    assert notifications_check._conflicts("owner/repo") == 0

    # A failed verification cannot suppress potential conflict work.
    git("update-ref", "-d", "refs/pull/1/head")
    assert notifications_check._conflicts("owner/repo") == 1
