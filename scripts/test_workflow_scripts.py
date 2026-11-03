"""Maintenance behavior at the GitHub and process boundaries used by workflows."""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

import pytest

from _fakes import FakeGh
from scripts import (
    check_release_artifacts,
    init_review_tracking,
    publish_release,
    reseed_integration,
    smoke_proxy,
)


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    # Only external commands are replaced. The workflow calls these modules'
    # main functions, so validation, selection, ordering and files stay real.
    fake = FakeGh()

    def check_output(args, *, input=None, **kwargs):
        return fake(*args, input=input)

    def run(args, *, input=None, **kwargs):
        output = fake(*args, input=input)
        return subprocess.CompletedProcess(args, 0, stdout=output)

    monkeypatch.setattr(subprocess, "check_output", check_output)
    monkeypatch.setattr(subprocess, "run", run)
    return fake


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "bot-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "model-secret")


@pytest.mark.parametrize("missing", ["GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_reseed_refuses_empty_sources_before_writes(
    credentials,
    processes: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.setenv(missing, "")
    with pytest.raises(SystemExit, match="is empty"):
        reseed_integration.main()
    assert processes.calls == []


@pytest.mark.parametrize("main_exists", [False, True])
def test_reseed_reconciles_all_policy_pages_and_only_owned_secrets(
    credentials,
    processes: FakeGh,
    main_exists: bool,
) -> None:
    endpoint = "repos/tend-agent/tend-integration/environments/tend"
    policies = f"{endpoint}/deployment-branch-policies"
    processes.respond("gh", with_="")
    processes.respond(
        "gh",
        "api",
        "--paginate",
        "--slurp",
        policies,
        with_=[
            {"branch_policies": [{"name": "probe", "id": 1, "type": "branch"}]},
            {
                "branch_policies": [{"name": "main", "id": 2, "type": "branch"}]
                if main_exists
                else []
            },
            {"branch_policies": [{"name": "stale", "id": 3, "type": "branch"}]},
            {"branch_policies": [{"name": "main", "id": 4, "type": "tag"}]},
        ],
    )
    processes.respond(
        "gh",
        "secret",
        "list",
        with_=[
            {"name": "TEND_BOT_TOKEN"},
            {"name": "OTHER_SECRET"},
            {"name": "CLAUDE_CODE_OAUTH_TOKEN"},
        ],
    )
    reseed_integration.main()
    assert json.loads(processes.stdins[0]) == {
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        }
    }
    assert processes.called("gh", "api", "-X", "DELETE") == [
        ("gh", "api", "-X", "DELETE", f"{policies}/1"),
        ("gh", "api", "-X", "DELETE", f"{policies}/3"),
        ("gh", "api", "-X", "DELETE", f"{policies}/4"),
    ]
    assert processes.called("gh", "api", "-X", "POST") == (
        []
        if main_exists
        else [
            (
                "gh",
                "api",
                "-X",
                "POST",
                policies,
                "-f",
                "name=main",
                "-f",
                "type=branch",
            ),
        ]
    )
    writes = processes.called("gh", "secret", "set")
    assert [call[3] for call in writes] == ["TEND_BOT_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]
    assert all(
        call[4:] == ("--repo", "tend-agent/tend-integration", "--env", "tend")
        for call in writes
    )
    assert [processes.stdins[processes.calls.index(call)] for call in writes] == [
        "bot-secret",
        "model-secret",
    ]
    assert [call[3] for call in processes.called("gh", "secret", "delete")] == [
        "TEND_BOT_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ]


def test_reseed_does_not_publish_secrets_when_policy_repair_fails(
    credentials,
    processes: FakeGh,
) -> None:
    processes.respond("gh", "api", "-X", "PUT", with_="")
    processes.respond(
        "gh",
        "api",
        "--paginate",
        "--slurp",
        with_=[
            {"branch_policies": [{"name": "probe", "id": 7, "type": "branch"}]},
        ],
    )
    processes.respond("gh", "api", "-X", "DELETE", with_=1)
    with pytest.raises(subprocess.CalledProcessError):
        reseed_integration.main()
    assert processes.called("gh", "secret") == []


@pytest.mark.parametrize("existing", [False, True])
def test_tracking_uses_utc_month_and_reuses_oldest_match(
    processes: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    existing: bool,
) -> None:
    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is datetime.UTC
            return cls(2026, 10, 1, tzinfo=tz)

    monkeypatch.setattr(datetime, "datetime", FixedDateTime)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    processes.respond(
        "gh",
        "issue",
        "list",
        with_=[
            {"title": "review-reviewers-tracking: 2026-09", "number": 1},
            *(
                [
                    {"title": "review-reviewers-tracking: 2026-10", "number": 9},
                    {"title": "review-reviewers-tracking: 2026-10", "number": 7},
                ]
                if existing
                else []
            ),
        ],
    )
    processes.respond("gh", "issue", "create", with_="")
    init_review_tracking.main()
    assert processes.calls[0] == (
        "gh",
        "issue",
        "list",
        "-R",
        "owner/repo",
        "--state",
        "open",
        "--label",
        "review-reviewers-tracking",
        "--json",
        "number,title",
    )
    if existing:
        assert len(processes.calls) == 1
        assert capsys.readouterr().out == "Tracking issue for 2026-10 exists: #7\n"
    else:
        assert processes.calls[1] == (
            "gh",
            "issue",
            "create",
            "-R",
            "owner/repo",
            "--title",
            "review-reviewers-tracking: 2026-10",
            "--label",
            "review-reviewers-tracking",
            "-F",
            "-",
        )
        assert processes.stdins[1] == init_review_tracking.BODY


@pytest.mark.parametrize("placeholder", [False, True])
def test_release_artifacts_are_checked_and_placeholder_version_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    processes: FakeGh,
    placeholder: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    name = "tend-0.0.0.tar.gz" if placeholder else "tend-1.2.3.tar.gz"
    (dist / name).touch()
    wheel = name.removesuffix(".tar.gz") + "-py3-none-any.whl"
    (dist / wheel).touch()
    (dist / ".gitignore").write_text("*\n")
    processes.respond("uv", with_="")
    if placeholder:
        with pytest.raises(SystemExit, match="Invalid version number"):
            check_release_artifacts.main()
    else:
        check_release_artifacts.main()
    assert processes.calls == [
        (
            "uv",
            "tool",
            "run",
            "--from",
            "twine",
            "twine",
            "check",
            f"dist/{wheel}",
            f"dist/{name}",
        )
    ]


@pytest.mark.parametrize(
    "version, expected",
    [
        ("0.1.1", "\nExact notes.\n\n"),
        ("0.1.10", "Ten notes.\n"),
        ("0.1", None),
    ],
)
def test_release_publishes_only_exact_changelog_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    processes: FakeGh,
    version: str,
    expected: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REF_NAME", version)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 0.1.10\nTen notes.\n## 0.1.1\n\nExact notes.\n\n## 0.1.0\nOld.\n"
    )
    processes.respond("gh", "release", "create", with_="")
    if expected is None:
        with pytest.raises(SystemExit, match="No CHANGELOG.md section"):
            publish_release.main()
        assert processes.calls == []
    else:
        publish_release.main()
        assert (tmp_path / "notes.md").read_text() == expected
        assert processes.calls == [
            (
                "gh",
                "release",
                "create",
                version,
                "--repo",
                "owner/repo",
                "--title",
                version,
                "--notes-file",
                str(tmp_path / "notes.md"),
                "--verify-tag",
            )
        ]


@pytest.mark.parametrize("failure", [None, "startup", "listener", "certificate"])
def test_proxy_smoke_checks_real_startup_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 3:
            if failure != "listener":
                kwargs["stdout"].write("HTTP(S) proxy listening at 127.0.0.1:1234\n")
            if failure != "certificate":
                ca = tmp_path / "tend-mitm-conf" / "mitmproxy-ca-cert.pem"
                ca.parent.mkdir()
                ca.touch()
        return subprocess.CompletedProcess(
            args, 1 if failure == "startup" and len(calls) == 3 else 0
        )

    monkeypatch.setattr(subprocess, "run", run)
    if failure:
        with pytest.raises(
            SystemExit,
            match={
                "startup": "failed to start",
                "listener": "report its listener",
                "certificate": "create its CA",
            }[failure],
        ):
            smoke_proxy.main()
    else:
        smoke_proxy.main()
    assert calls[0][0][0] == "bash"
    assert calls[0][0][1].endswith("/shared/steps/install-uv.sh")
    assert calls[0][1]["env"]["UV_INSTALL_DIR"] == str(tmp_path / "tend-uv")
    command, options = calls[2]
    assert command[0] == str(tmp_path / "tend-uv/uv")
    assert command[1:6] == [
        "run",
        "--no-config",
        "--no-python-downloads",
        "--python",
        "/usr/bin/python3",
    ]
    assert command[8:13] == [
        "--frozen",
        "--no-build",
        "--only-group",
        "proxy",
        "mitmdump",
    ]
    assert command[-2:] == ["--allow-hosts", r"^example\.invalid$"]
    assert command[command.index("--listen-port") + 1] == "0"
    assert options["env"]["TEND_GH_TOKEN"] == "dummy"
    assert options["env"]["TEND_ANTHROPIC_OAUTH_TOKEN"] == "dummy"
    assert options["cwd"].name == "proxy"
    assert (tmp_path / "tend-empty.flows").read_bytes() == b""
