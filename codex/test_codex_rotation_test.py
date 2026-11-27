"""Exercise the rotation test's ordering and write-back without a server.

Only npm, the downloaded CLI, and gh are replaced. The test stages real auth
files and runs child processes as the live workflow does.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner
from ruamel.yaml import YAML
from tend.runtime.codex import codex_rotation_test

LOGIN = {
    "auth_mode": "chatgpt",
    "tokens": {
        "access_token": "old-access",
        "id_token": "identity",
        "account_id": "account",
        "refresh_token": "old-refresh",
    },
}

FAKE_CODEX = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
assert "GH_TOKEN" not in os.environ and "CODEX_AUTH_JSON" not in os.environ
path = Path(os.environ["CODEX_HOME"], "auth.json")
auth = json.loads(path.read_text())
args = sys.argv[1:]
behavior = os.environ["ROTATION_BEHAVIOR"]
if "--output-last-message" in args:
    assert auth["auth_mode"] == "chatgptAuthTokens"
    assert auth["tokens"]["refresh_token"] == ""
    assert auth["tokens"]["access_token"] == "new-access"
    Path(args[args.index("--output-last-message") + 1]).write_text("MODEL_OK")
    sys.exit(0)
assert auth["last_refresh"] == "1970-01-01T00:00:00Z"
assert auth["tokens"]["refresh_token"] == "old-refresh"
if behavior != "no-refresh":
    auth["tokens"]["access_token"] = "new-access"
    auth["tokens"]["refresh_token"] = "new-refresh"
    auth["last_refresh"] = "2026-11-27T00:00:00Z"
    path.write_text(json.dumps(auth))
sys.exit(1 if behavior == "exit-after-refresh" else 0)
"""

FAKE_NPM = """#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
assert "--ignore-scripts" in args and "GH_TOKEN" not in os.environ
Path(os.environ["ROTATION_PACKAGE"]).write_text(args[-1])
target = Path(args[args.index("--prefix") + 1], "node_modules/.bin/codex")
target.parent.mkdir(parents=True)
shutil.copy2(os.environ["ROTATION_CLI"], target)
"""

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["ROTATION_GH_LOG"], "a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "value": json.loads(sys.stdin.read())}) + "\\n")
"""


def _install_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, body in (("fake-codex", FAKE_CODEX), ("npm", FAKE_NPM), ("gh", FAKE_GH)):
        path = tmp_path / name
        path.write_text(body)
        path.chmod(0o755)
    log = tmp_path / "gh.log"
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ROTATION_CLI", str(tmp_path / "fake-codex"))
    monkeypatch.setenv("ROTATION_PACKAGE", str(tmp_path / "package"))
    monkeypatch.setenv("ROTATION_GH_LOG", str(log))
    monkeypatch.setenv("CODEX_AUTH_JSON", json.dumps(LOGIN))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    return log


def _writes(log: Path) -> list[dict]:
    if not log.exists():
        return []
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    for entry in entries:
        assert entry["args"] == [
            "secret",
            "set",
            "CODEX_AUTH_JSON",
            "--repo",
            "owner/repo",
            "--env",
            "codex-rotation-test",
        ]
    return [entry["value"] for entry in entries]


def _step(command: str, *args: str, pat: bool) -> object:
    """Run one workflow step, with the write-back token only where it belongs."""
    env = {"GH_TOKEN": "test-pat" if pat else None}
    return CliRunner().invoke(codex_rotation_test.main, [command, *args], env=env)


@pytest.mark.parametrize("behavior", ["success", "exit-after-refresh", "no-refresh"])
def test_rotation_persists_before_judging_and_uses_access_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behavior: str
) -> None:
    log = _install_fakes(tmp_path, monkeypatch)
    monkeypatch.setenv("ROTATION_BEHAVIOR", behavior)
    state = ["--state", str(tmp_path / "state")]
    version = ["--codex-version", "9.9.9"]

    checked = _step("check-write", pat=True)
    assert checked.exit_code == 0, checked.output
    # The unchanged login is written first, proving write access before rotation.
    assert _writes(log) == [LOGIN]

    rotated = _step("rotate", *version, *state, pat=False)
    assert (rotated.exit_code == 0) is (behavior != "exit-after-refresh"), (
        rotated.output
    )
    assert (tmp_path / "package").read_text() == "@openai/codex@9.9.9"

    # The workflow persists whatever Codex wrote, even after a failed request.
    persisted = _step("persist", *state, pat=True)
    if behavior == "no-refresh":
        assert persisted.exit_code != 0
        assert "did not refresh" in persisted.output
        assert _writes(log) == [LOGIN]
        return
    assert persisted.exit_code == 0, persisted.output
    writes = _writes(log)
    assert len(writes) == 2
    assert writes[1]["tokens"]["access_token"] == "new-access"
    assert writes[1]["tokens"]["refresh_token"] == "new-refresh"
    if behavior == "success":
        verified = _step("verify", *version, "--model", "m", *state, pat=False)
        assert verified.exit_code == 0, verified.output
        assert "refresh token rotated" in verified.output
        assert "PASS access-only bundle: m response" in verified.output


def test_write_back_requires_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _install_fakes(tmp_path, monkeypatch)

    result = _step("check-write", pat=False)

    assert result.exit_code != 0
    assert "CODEX_ROTATION_TEST_PAT" in result.output
    assert _writes(log) == []


@pytest.mark.parametrize("command", ["rotate", "verify"])
def test_candidate_steps_refuse_a_github_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    _install_fakes(tmp_path, monkeypatch)
    monkeypatch.setenv("ROTATION_BEHAVIOR", "success")

    result = _step(command, "--codex-version", "1", "--state", str(tmp_path), pat=True)

    assert result.exit_code != 0
    assert "must not run under a process that holds one" in result.output
    assert not (tmp_path / "package").exists()


def test_refresh_request_matches_the_refresh_action() -> None:
    action = YAML(typ="safe").load(
        (Path(__file__).parent / "refresh/action.yaml").read_text()
    )
    step = next(s for s in action["runs"]["steps"] if s.get("id") == "codex")
    assert shlex.split(step["run"].removesuffix(" >/dev/null")) == [
        "codex",
        *codex_rotation_test.REFRESH_ARGS,
    ]
