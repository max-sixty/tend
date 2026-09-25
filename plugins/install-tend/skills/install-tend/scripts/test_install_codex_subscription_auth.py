"""Tests for the install-time Codex subscription provisioning helper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import install_codex_subscription_auth as auth_module
import pytest
from install_codex_subscription_auth import (
    CONSUMER_AUTH_MODE,
    CONSUMER_SECRET,
    FULL_SECRET,
    REFRESH_PAT_SECRET,
    ProvisionError,
    consumer_auth,
    pat_url,
    provision,
    store_pat,
)

FULL_AUTH = {
    "auth_mode": "chatgpt",
    "OPENAI_API_KEY": "stale",
    "tokens": {
        "access_token": "access",
        "refresh_token": "refresh",
        "id_token": "id",
        "account_id": "account",
    },
}

ACTION_AUTH_SCRIPT = (
    Path(__file__).parents[5] / "shared" / "steps" / "codex_subscription_auth.py"
)


def _executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)


def test_consumer_auth_removes_refresh_capability() -> None:
    consumer = consumer_auth(FULL_AUTH)

    assert consumer["auth_mode"] == CONSUMER_AUTH_MODE
    assert consumer["OPENAI_API_KEY"] is None
    assert consumer["tokens"]["refresh_token"] == ""
    assert consumer["tokens"]["access_token"] == "access"
    assert FULL_AUTH["tokens"]["refresh_token"] == "refresh"


def test_consumer_auth_is_accepted_by_action(tmp_path: Path) -> None:
    destination = tmp_path / "auth.json"
    consumer = consumer_auth(FULL_AUTH)
    result = subprocess.run(
        [sys.executable, ACTION_AUTH_SCRIPT, "prepare", destination],
        env={
            **os.environ,
            "CODEX_AUTH_JSON": json.dumps(consumer),
            "OPENAI_API_KEY": "",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(destination.read_text()) == consumer


@pytest.mark.parametrize(
    ("bundle", "message"),
    [
        ({"auth_mode": "apiKey"}, "auth_mode 'chatgpt'"),
        ({"auth_mode": "chatgpt"}, "missing tokens"),
        (
            {"auth_mode": "chatgpt", "tokens": {}},
            "missing tokens.access_token",
        ),
    ],
)
def test_consumer_auth_rejects_incomplete_login(
    bundle: dict[str, object], message: str
) -> None:
    with pytest.raises(ProvisionError, match=message):
        consumer_auth(bundle)


def test_pat_url_prefills_owner_and_minimal_permission() -> None:
    url = pat_url("owner/repo")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query == {
        "description": ["Rotates Codex subscription credentials for owner/repo"],
        "environments": ["write"],
        "expires_in": ["none"],
        "name": ["Tend Codex refresh: owner/repo"],
        "target_name": ["owner"],
    }


def test_pat_url_name_stays_specific_for_long_repositories() -> None:
    first = urllib.parse.parse_qs(
        urllib.parse.urlparse(pat_url(f"owner/{'a' * 50}")).query
    )["name"][0]
    second = urllib.parse.parse_qs(
        urllib.parse.urlparse(pat_url(f"owner/{'a' * 49}b")).query
    )["name"][0]

    assert len(first) <= 40
    assert len(second) <= 40
    assert first != second


@pytest.mark.parametrize("repository", ["owner", "/repo", "owner/", "a/b/c"])
def test_pat_url_rejects_malformed_repository(repository: str) -> None:
    with pytest.raises(ProvisionError, match="OWNER/REPO"):
        pat_url(repository)


def test_provision_rejects_malformed_repository_before_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "install_codex_subscription_auth._codex_command", lambda: pytest.fail()
    )

    with pytest.raises(ProvisionError, match="OWNER/REPO"):
        provision("owner")


def test_provision_streams_login_stores_bundles_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    secret_log = tmp_path / "secrets.jsonl"
    home_log = tmp_path / "codex-home"
    gate_log = tmp_path / "refresh-gate"

    _executable(
        bin_dir / "codex",
        """
import json
import os
import sys
from pathlib import Path

assert sys.argv[1:] == [
    "-c", 'cli_auth_credentials_store="file"', "login", "--device-auth"
]
home = Path(os.environ["CODEX_HOME"])
assert Path(os.environ["TEND_TEST_GATE_LOG"]).read_text() == "main"
Path(os.environ["TEND_TEST_HOME_LOG"]).write_text(str(home))
(home / "auth.json").write_text(os.environ["TEND_TEST_AUTH_JSON"])
""",
    )
    _executable(
        bin_dir / "gh",
        """
import json
import os
import sys
from pathlib import Path

log = Path(os.environ["TEND_TEST_SECRET_LOG"])
if sys.argv[1:3] == ["secret", "set"]:
    with log.open("a") as file:
        file.write(json.dumps({"name": sys.argv[3], "env": sys.argv[-1], "value": sys.stdin.read()}) + "\\n")
elif sys.argv[1:3] == ["secret", "list"]:
    names = [item["name"] for line in log.read_text().splitlines()
             if (item := json.loads(line))["env"] == sys.argv[-3]]
    print(json.dumps([{"name": name} for name in names]))
elif sys.argv[1] == "api":
    if ".default_branch" in sys.argv:
        print("main")
    elif "PUT" in sys.argv:
        policy = json.loads(sys.stdin.read())["deployment_branch_policy"]
        assert policy == {"protected_branches": False, "custom_branch_policies": True}
    elif "POST" in sys.argv:
        branch = next(arg[5:] for arg in sys.argv if arg.startswith("name="))
        Path(os.environ["TEND_TEST_GATE_LOG"]).write_text(branch)
else:
    raise SystemExit(f"unexpected gh arguments: {sys.argv[1:]}")
""",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TEND_TEST_AUTH_JSON", json.dumps(FULL_AUTH))
    monkeypatch.setenv("TEND_TEST_HOME_LOG", str(home_log))
    monkeypatch.setenv("TEND_TEST_GATE_LOG", str(gate_log))
    monkeypatch.setenv("TEND_TEST_SECRET_LOG", str(secret_log))

    provision("owner/repo")

    stored = {
        (item["env"], item["name"]): json.loads(item["value"])
        for item in map(json.loads, secret_log.read_text().splitlines())
    }
    assert stored[("tend-codex-refresh", FULL_SECRET)] == FULL_AUTH
    assert stored[("tend", CONSUMER_SECRET)] == consumer_auth(FULL_AUTH)
    assert not Path(home_log.read_text()).exists()


def test_store_pat_rejects_other_input() -> None:
    with pytest.raises(ProvisionError, match="fine-grained GitHub PAT"):
        store_pat("owner/repo", "not-a-token")


def test_store_pat_sets_and_verifies_all_subscription_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    secret_log = tmp_path / "secrets.jsonl"
    secret_log.write_text(
        "\n".join(
            json.dumps({"name": name, "env": environment, "value": "auth"})
            for environment, name in (
                ("tend-codex-refresh", FULL_SECRET),
                ("tend", CONSUMER_SECRET),
            )
        )
        + "\n"
    )
    _executable(
        bin_dir / "gh",
        """
import json
import os
import sys
from pathlib import Path

log = Path(os.environ["TEND_TEST_SECRET_LOG"])
if sys.argv[1:3] == ["secret", "set"]:
    value = sys.stdin.read()
    assert os.environ["GH_HOST"] == "github.com"
    with log.open("a") as file:
        file.write(json.dumps({"name": sys.argv[3], "env": sys.argv[-1], "value": value, "self_auth": os.environ.get("GH_TOKEN") == value}) + "\\n")
elif sys.argv[1:3] == ["secret", "list"]:
    names = [item["name"] for line in log.read_text().splitlines()
             if (item := json.loads(line))["env"] == sys.argv[-3]]
    print(json.dumps([{"name": name} for name in names]))
elif sys.argv[1] == "api":
    if ".default_branch" in sys.argv:
        print("main")
else:
    raise SystemExit(f"unexpected gh arguments: {sys.argv[1:]}")
""",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_HOST", "enterprise.example")
    monkeypatch.setenv("TEND_TEST_SECRET_LOG", str(secret_log))

    store_pat("owner/repo", "  github_pat_secret\n")

    stored = [json.loads(line) for line in secret_log.read_text().splitlines()]
    assert stored[-1] == {
        "name": REFRESH_PAT_SECRET,
        "env": "tend-codex-refresh",
        "value": "github_pat_secret",
        "self_auth": True,
    }


def test_store_pat_reports_pat_without_environment_write_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(
        bin_dir / "gh",
        """
import os
import sys

if sys.argv[1] == "api":
    if ".default_branch" in sys.argv:
        print("main")
elif sys.argv[1:3] == ["secret", "list"]:
    raise AssertionError("checked secrets after failed PAT write")
elif sys.argv[1:3] == ["secret", "set"]:
    assert os.environ["GH_TOKEN"] == "github_pat_without_access"
    print("HTTP 403: Resource not accessible by personal access token", file=sys.stderr)
    raise SystemExit(1)
""",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(ProvisionError, match="Environments: Read and write"):
        store_pat("owner/repo", "github_pat_without_access")


def test_store_pat_prompts_without_a_clipboard_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TerminalInput:
        def isatty(self) -> bool:
            return True

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(auth_module.sys, "stdin", TerminalInput())
    monkeypatch.setattr(
        auth_module.sys,
        "argv",
        ["install_codex_subscription_auth.py", "store-pat", "--repo", "owner/repo"],
    )
    monkeypatch.setattr(
        auth_module.getpass, "getpass", lambda prompt: "github_pat_secret"
    )
    monkeypatch.setattr(
        auth_module, "store_pat", lambda repo, token: calls.append((repo, token))
    )

    auth_module.main()

    assert calls == [("owner/repo", "github_pat_secret")]
