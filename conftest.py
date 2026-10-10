"""Shared fixtures for the sandbox and harness tests.

``fake_gh`` replaces ``_common.gh`` for the test, so a step's GitHub calls hit
canned responses and are recorded, and a test never puts a shim on ``PATH``.
``github_files`` points the runner's file channels (``GITHUB_OUTPUT``,
``GITHUB_ENV``, ``GITHUB_STEP_SUMMARY``) at files under ``tmp_path``.
``actions_env`` sets the run and event variables a step reads to name itself.
The doubles themselves live in ``_fakes.py`` so tests can import their types.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tend.runtime.shared import _common

from _fakes import FakeGh, GithubFiles


@pytest.fixture
def fake_gh(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(_common, "gh", fake)
    return fake


@pytest.fixture
def github_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GithubFiles:
    files = GithubFiles(tmp_path / "output", tmp_path / "env", tmp_path / "summary")
    for path in (files.output, files.env, files.summary):
        path.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(files.output))
    monkeypatch.setenv("GITHUB_ENV", str(files.env))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(files.summary))
    return files


@pytest.fixture
def actions_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The Actions run/event variables, with a pull_request event on disk.

    Returns the event payload's path, so a test that needs a different trigger
    rewrites the file and resets ``GITHUB_EVENT_NAME``.
    """
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 851}}))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    return event


@pytest.fixture
def installed_runtime(tmp_path: Path) -> tuple[Path, Path]:
    """Install the candidate package non-editably, as the action does.

    Isolated children cannot import from the checkout or pytest's module cache.
    """
    import hashlib
    import os
    import subprocess
    import sys

    checkout = Path(__file__).resolve().parent
    venv = tmp_path / "venv"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("UV_") or key == "UV_CACHE_DIR"
    }
    environment["UV_PROJECT_ENVIRONMENT"] = str(venv)
    subprocess.run(
        [
            "uv",
            "sync",
            "--no-config",
            "--offline",
            "--no-python-downloads",
            "--python",
            sys.executable,
            "--project",
            str(checkout),
            "--package",
            "tend",
            "--frozen",
            "--no-dev",
            "--no-editable",
            "--link-mode",
            "copy",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    python = venv / "bin/python"
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import pathlib, tend; print(pathlib.Path(tend.__file__).parent)",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    package = Path(result.stdout.strip())
    assert package.is_relative_to(venv)
    source = checkout / "generator/src/tend"
    source_hashes = {
        path.relative_to(source): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*.py")
    }
    installed_hashes = {
        path.relative_to(package): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package.rglob("*.py")
    }
    assert installed_hashes == source_hashes, (
        "Installed Tend package differs from the candidate Python source"
    )
    return python, package
