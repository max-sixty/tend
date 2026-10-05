"""Exercise the live probe's credential and completion boundary without a server.

Only npm/the downloaded CLI are replaced. The probe stages actual auth files,
launches child processes, and handles their results as it does in the live gate.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from tend.runtime.codex import codex_model_smoke


@pytest.mark.parametrize("behavior", ["success", "empty", "failure"])
def test_smoke_requires_response_without_exposing_refresh_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behavior: str
) -> None:
    login = tmp_path / "saved-login.json"
    login.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "access",
                    "id_token": "identity",
                    "account_id": "account",
                    "refresh_token": "saved-refresh",
                },
            }
        )
    )
    before = login.read_bytes()
    cli = tmp_path / "fake-codex"
    cli.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
auth = json.loads(Path(os.environ["CODEX_HOME"], "auth.json").read_text())
assert auth["auth_mode"] == "chatgptAuthTokens"
assert auth["tokens"]["refresh_token"] == ""
assert "OPENAI_API_KEY" not in os.environ and "CODEX_API_KEY" not in os.environ
assert "CODEX_AUTH_JSON" not in os.environ
args = sys.argv[1:]
Path(os.environ["SMOKE_MODEL"]).write_text(args[args.index("--model")+1])
if os.environ["SMOKE_BEHAVIOR"] == "failure": sys.exit(1)
if os.environ["SMOKE_BEHAVIOR"] == "success":
    Path(args[args.index("--output-last-message")+1]).write_text("MODEL_OK")
""")
    cli.chmod(0o755)
    npm = tmp_path / "npm"
    npm.write_text("""#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["SMOKE_PACKAGE"]).write_text(args[-1])
target = Path(args[args.index("--prefix")+1], "node_modules/.bin/codex")
target.parent.mkdir(parents=True)
shutil.copy2(os.environ["SMOKE_CLI"], target)
""")
    npm.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SMOKE_CLI", str(cli))
    monkeypatch.setenv("SMOKE_BEHAVIOR", behavior)
    monkeypatch.setenv("SMOKE_MODEL", str(tmp_path / "model"))
    monkeypatch.setenv("SMOKE_PACKAGE", str(tmp_path / "package"))
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-auth-mode")
    monkeypatch.setenv("CODEX_API_KEY", "wrong-auth-mode")
    result = CliRunner().invoke(
        codex_model_smoke.main,
        [
            "--auth-file",
            str(login),
            "--repository",
            str(Path(__file__).resolve().parents[1]),
        ],
    )
    assert (result.exit_code == 0) is (behavior == "success"), result.output
    assert login.read_bytes() == before
    assert (
        tmp_path / "model"
    ).read_text() == codex_model_smoke.DEFAULT_MODEL_BY_HARNESS["codex"]
    repository = Path(__file__).resolve().parents[1]
    action = codex_model_smoke.YAML(typ="safe").load(
        (repository / "codex/action.yaml").read_text()
    )
    assert (
        tmp_path / "package"
    ).read_text() == f"@openai/codex@{action['inputs']['codex_version']['default']}"


def test_smoke_fails_when_credentials_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_AUTH_JSON", raising=False)
    result = CliRunner().invoke(codex_model_smoke.main)
    assert result.exit_code != 0
    assert "Supply access-only CODEX_AUTH_JSON or --auth-file" in result.output
