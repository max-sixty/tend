"""The installed Tend CLI owns runtime arguments, outcomes and action metadata."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from tend.cli import main
from tend.runtime.codex import codex_model_smoke
from tend.runtime.codex import runner as codex_runner
from tend.runtime.shared import mark_notification_read


def test_runtime_help_and_argument_errors() -> None:
    runner = CliRunner()
    for arguments in (
        ["runtime", "--help"],
        ["runtime", "codex", "--help"],
        ["runtime", "codex", "auth", "--help"],
        ["runtime", "codex", "model-smoke", "--help"],
        ["runtime", "codex", "check-cli", "--help"],
        ["runtime", "claude", "--help"],
        ["runtime", "gist-memory", "--help"],
    ):
        result = runner.invoke(main, arguments)
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output
    for arguments in (
        ["runtime", "token-usage"],
        ["runtime", "token-usage", "--harness", "other"],
        ["runtime", "codex", "auth", "prepare"],
        ["runtime", "copy-session-tree", "source", "dest", "-1", "1"],
    ):
        result = runner.invoke(main, arguments)
        assert result.exit_code == 2, result.output
        assert "Error:" in result.output


def test_auth_cli_publishes_mode_and_rejects_invalid_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.touch()
    auth = tmp_path / "auth.json"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    consumer = {
        "auth_mode": "chatgptAuthTokens",
        "tokens": {
            "access_token": "access",
            "refresh_token": "",
            "id_token": "id",
            "account_id": "account",
        },
    }
    monkeypatch.setenv("CODEX_AUTH_JSON", json.dumps(consumer))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(
        main, ["runtime", "codex", "auth", "prepare", str(auth)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(auth.read_text()) == consumer
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600
    assert output.read_text() == "mode=subscription\n"
    assert "Codex auth: using subscription" in result.output

    monkeypatch.setenv("CODEX_AUTH_JSON", "invalid-json")
    result = CliRunner().invoke(
        main, ["runtime", "codex", "auth", "prepare", str(auth)]
    )
    assert result.exit_code == 1, result.output
    assert "Error: CODEX_AUTH_JSON is not valid JSON" in result.output
    assert json.loads(auth.read_text()) == consumer


def test_runtime_preserves_operation_exit_and_subprocess_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An operation result must become the process exit, rather than Click's
    # default success when a callback merely returns a nonzero integer.
    monkeypatch.setattr(codex_runner, "run_codex", lambda: 7)
    result = CliRunner().invoke(main, ["runtime", "codex", "run"])
    assert result.exit_code == 7

    def failed_operation() -> int:
        raise subprocess.CalledProcessError(9, ["gh", "api", "notifications"])

    monkeypatch.setattr(mark_notification_read, "main", failed_operation)
    result = CliRunner().invoke(main, ["runtime", "mark-notification-read"])
    assert result.exit_code == 1, result.exception
    assert "::error::gh api notifications failed (exit 9)" in result.output


def test_model_smoke_cli_projects_auth_and_passes_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = tmp_path / "auth.json"
    original = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": "id",
            "account_id": "account",
        },
    }
    auth.write_text(json.dumps(original))
    calls: list[tuple[Path, dict[str, object]]] = []
    # Real model behavior has its own live test; capture only that external
    # operation here so the root command's auth projection is exercised.
    monkeypatch.setattr(
        codex_model_smoke,
        "verify",
        lambda repository, auth_json: calls.append((repository, json.loads(auth_json))),
    )
    result = CliRunner().invoke(
        main,
        [
            "runtime",
            "codex",
            "model-smoke",
            "--repository",
            str(tmp_path),
            "--auth-file",
            str(auth),
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        (
            tmp_path,
            {
                "auth_mode": "chatgptAuthTokens",
                "tokens": {
                    "access_token": "access",
                    "refresh_token": "",
                    "id_token": "id",
                    "account_id": "account",
                },
                "OPENAI_API_KEY": None,
            },
        )
    ]
    assert json.loads(auth.read_text()) == original


def test_installed_console_and_isolated_package_entrypoint(
    installed_runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    python, _ = installed_runtime
    # The checkout is absent from the child import path, and a consumer-owned
    # Python package in cwd must never shadow the installed action package.
    hostile = tmp_path / "tend"
    hostile.mkdir()
    (hostile / "__init__.py").write_text('raise RuntimeError("consumer import")\n')
    for command in (
        [str(python.parent / "tend")],
        [str(python), "-I", "-m", "tend"],
    ):
        result = subprocess.run(
            [
                *command,
                "runtime",
                "codex",
                "auth",
                "prepare",
                str(tmp_path / "auth.json"),
            ],
            cwd=tmp_path,
            env={"OPENAI_API_KEY": "isolated-key"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "Codex auth: using api-key\n"
        assert not (tmp_path / "auth.json").exists()


def test_portable_cli_help_does_not_load_linux_runtime_dependencies(
    installed_runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    python, _ = installed_runtime
    # A fresh interpreter has no pytest-imported modules to mask an eager
    # import. Refuse platform-only modules as a Windows interpreter would.
    script = """
import importlib.abc
import sys

sys.modules.pop("pwd", None)
sys.modules.pop("resource", None)

class RejectLinuxModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"pwd", "resource"}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, RejectLinuxModules())
from tend.cli import main
main(prog_name="tend")
"""
    for arguments in (["--help"], ["init", "--help"], ["runtime", "--help"]):
        result = subprocess.run(
            [str(python), "-I", "-c", script, *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Usage: tend" in result.stdout
