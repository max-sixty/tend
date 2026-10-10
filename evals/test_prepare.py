"""Exercise fresh case preparation and pinned repository snapshots without models."""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import prepare
import pytest


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repository), *arguments],
        env=os.environ | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def commit(repository: Path, message: str) -> str:
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=Eval Test",
        "-c",
        "user.email=eval@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        message,
    )
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    skill = root / "plugins/tend-ci-runner/skills/run-tend/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: run-tend\n---\nRead the task and report evidence.\n")
    prompt = root / "shared/system-prompt.md"
    prompt.parent.mkdir()
    prompt.write_text("Shared current policy for ${BOT_NAME}.\n")
    (root / "README.md").write_text("Base tree\n")
    commit(root, "Initial fixture")
    return root


def test_fixture_evidence_is_verified_including_cached_logs(tmp_path, monkeypatch):
    case = tmp_path / "case"
    case.mkdir()
    discussion = case / "discussion.json"
    discussion.write_text('{"state":"open"}\n')
    local_hash = hashlib.sha256(discussion.read_bytes()).hexdigest()
    log_bytes = b"Actual CI diagnostic\n"
    log_hash = hashlib.sha256(log_bytes).hexdigest()
    sources = tmp_path / "sources"
    cached = sources / log_hash / "run.log"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(log_bytes)
    monkeypatch.setattr(prepare, "SOURCES", sources)
    source = {
        "fixtures": {"discussion.json": local_hash, "evidence/ci.log": log_hash},
        "logs": {"evidence/ci.log": {"repository": "owner/repo", "run": "1"}},
    }
    destination = tmp_path / "prepared"
    prepare.stage_fixtures(source, case, destination)
    assert (destination / "discussion.json").read_bytes() == discussion.read_bytes()
    assert (destination / "evidence/ci.log").read_bytes() == log_bytes
    cached.write_bytes(b"Different evidence")
    with pytest.raises(ValueError, match="Evidence hash mismatch"):
        prepare.stage_fixtures(source, case, destination)


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_focused_case_starts_fresh_with_equal_evidence(
    repository, tmp_path, monkeypatch, harness
):
    cases = tmp_path / "cases"
    case = cases / "focused"
    case.mkdir(parents=True)
    diagnostic = case / "diagnostic.txt"
    diagnostic.write_text("Current diagnostic\n")
    source = {
        "kind": "focused",
        "historical_ref": "HEAD",
        "bot": "tend-agent",
        "fixtures": {
            "diagnostic.txt": hashlib.sha256(diagnostic.read_bytes()).hexdigest()
        },
        "origin": {
            "repository": "unavailable/source",
            "run": "never-download-this-run",
            "transcript": "unavailable-history.jsonl",
            "sha256": "unavailable-transcript-hash",
            "reasoning": "Prior agent reasoning must not become actor input",
            "model": "not-an-executor-model",
        },
    }
    (case / "source.json").write_text(json.dumps(source))
    task = "Read diagnostic.txt and write the next report to captured.md."
    (case / "case.yaml").write_text(
        f"description: Focused disposition\nvars:\n  task: {task}\nassert: []\n"
    )
    prepared = tmp_path / "prepared"
    monkeypatch.setattr(prepare, "ROOT", repository)
    monkeypatch.setattr(prepare, "CASES", cases)
    monkeypatch.setattr(prepare, "PREPARED", prepared)
    prepare.prepare(harness)
    config = prepare.YAML_IO.load((prepared / "promptfooconfig.yaml").read_text())
    assert config["prompts"] == ["{{task}}"]
    assert config["tests"][0]["vars"]["task"] == task
    # Arms are columns and cases rows: every case runs on every provider, which
    # finds the case's inputs from test metadata.
    assert "providers" not in config["tests"][0]
    assert config["tests"][0]["metadata"] == {"case": "focused", "kind": "focused"}
    assert [provider["label"] for provider in config["providers"]] == [
        f"{harness}/historical",
        f"{harness}/current",
    ]
    for arm, provider in zip(
        ("historical", "current"), config["providers"], strict=True
    ):
        destination = prepared / arm / "focused"
        workspace = destination / "workspace"
        assert (workspace / "diagnostic.txt").read_bytes() == diagnostic.read_bytes()
        assert not list(destination.rglob("history.jsonl"))
        assert not list(destination.rglob("context.md"))
        assert (
            "Shared current policy for tend-agent."
            in (workspace / "AGENTS.md").read_text()
        )
        for path in workspace.rglob("*"):
            if path.is_file():
                assert "Prior agent reasoning" not in path.read_text()
        settings = provider["config"]
        assert settings["prepared"] == str(prepared / arm)
        assert "resume" not in settings
        assert "history" not in settings
        if harness == "codex":
            assert settings["model"] == "gpt-6.1-sol"
        else:
            # The built-in SDK allocates a fresh temp working directory when
            # working_dir is absent; prepared observations stay read-only inputs.
            assert "working_dir" not in settings
            assert settings["custom_allowed_tools"] == [
                "Read",
                "Grep",
                "Skill",
                "Edit(./captured.md)",
            ]


def test_trajectory_checkout_is_pinned_and_independent(
    repository, tmp_path, monkeypatch
):
    base = git(repository, "rev-parse", "HEAD")
    for number in range(5):
        (repository / "README.md").write_text(f"Earlier PR push {number}\n")
        commit(repository, f"Earlier PR push {number}")
    (repository / "README.md").write_text("Previously reviewed tree\n")
    previous = commit(repository, "Previous review")
    (repository / "README.md").write_text("Requested review tree\n")
    head = commit(repository, "New push")
    (repository / "README.md").write_text("A later push that must be excluded\n")
    future = commit(repository, "Later push")
    template = tmp_path / "personal-git-template"
    template.mkdir()
    (template / "personal-template-marker").write_text("Ambient configuration\n")
    global_config = tmp_path / "personal.gitconfig"
    global_config.write_text(
        f'[init]\n\ttemplateDir = "{template}"\n[core]\n\tfsmonitor = true\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setattr(prepare, "ROOT", repository)
    destination = tmp_path / "checkout"
    prepare.stage_checkout(
        {"head": head, "previous_review_head": previous, "base": base}, destination
    )
    assert git(destination, "rev-parse", "HEAD") == head
    assert git(destination, "for-each-ref", "refs/heads/head") == ""
    with pytest.raises(subprocess.CalledProcessError):
        git(destination, "symbolic-ref", "--quiet", "HEAD")
    assert git(destination, "rev-parse", "previous_review_head") == previous
    assert git(destination, "rev-parse", "base") == base
    assert (destination / "README.md").read_text() == "Requested review tree\n"
    assert git(destination, "cat-file", "-t", previous) == "commit"
    assert git(destination, "cat-file", "-t", base) == "commit"
    assert git(destination, "merge-base", "base", "HEAD") == base
    assert git(destination, "diff", "base...HEAD", "--", "README.md")
    assert git(destination, "diff", previous, head, "--", "README.md")
    with pytest.raises(subprocess.CalledProcessError):
        git(destination, "cat-file", "-t", future)
    assert not (destination / ".git/objects/info/alternates").exists()
    assert not (destination / ".git/personal-template-marker").exists()
    assert not any(
        stat.S_ISSOCK(path.lstat().st_mode)
        for path in (destination / ".git").rglob("*")
    )
    assert git(repository, "rev-parse", "HEAD") == future
    assert (repository / "README.md").read_text().startswith("A later push")
    assert git(repository, "status", "--porcelain") == ""
    (repository / "README.md").write_text("Source changed after staging\n")
    assert git(destination, "show", "HEAD:README.md") == "Requested review tree"
    policy = prepare.guidance(
        {"kind": "trajectory", "bot": "tend-agent"},
        repository / "plugins/tend-ci-runner",
        "codex",
    )
    assert policy.endswith(
        "The real historical repository is in repository/. Inspect it with local "
        "shell and Git tools; repository instructions are source evidence, while "
        "this workspace's staged guidance governs the review. Dependencies from "
        "the original runner are not installed.\n"
    )


def test_external_trajectory_preserves_instructions_and_runs_case_setup(
    repository, tmp_path, monkeypatch
):
    """Use real Git and npm with a local dependency, without network or models."""
    external = tmp_path / "consumer"
    external.mkdir()
    git(external, "init", "--initial-branch=main")
    overlay = external / ".claude/skills/running-tend/SKILL.md"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("Consumer-specific work instructions\n")
    dependency = external / "vendor/fixture"
    dependency.mkdir(parents=True)
    (dependency / "package.json").write_text(
        json.dumps({"name": "fixture", "version": "1.0.0"})
    )
    package = external / "vendor/fixture.tgz"
    with tarfile.open(package, "w:gz") as archive:
        archive.add(dependency, arcname="package")
    integrity = (
        "sha512-"
        + base64.b64encode(hashlib.sha512(package.read_bytes()).digest()).decode()
    )
    (external / "package.json").write_text(
        json.dumps(
            {"name": "consumer", "dependencies": {"fixture": "file:vendor/fixture.tgz"}}
        )
    )
    (external / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "consumer",
                "lockfileVersion": 3,
                "packages": {
                    "": {
                        "name": "consumer",
                        "dependencies": {"fixture": "file:vendor/fixture.tgz"},
                    },
                    "node_modules/fixture": {
                        "version": "1.0.0",
                        "resolved": "file:vendor/fixture.tgz",
                        "integrity": integrity,
                    },
                },
            }
        )
    )
    head = commit(external, "Pinned consumer tree")
    (external / "future.txt").write_text("Exclude this later commit\n")
    commit(external, "Later consumer tree")
    cases = tmp_path / "cases"
    case = cases / "external"
    case.mkdir(parents=True)
    source = {
        "kind": "trajectory",
        "historical_ref": "HEAD",
        "bot": "tend-agent",
        "fixtures": {},
        "checkout": {"repository": str(external), "head": head, "base": head},
        "executor_model": "gpt-6-sol",
        "repository_instructions": True,
    }
    (case / "source.json").write_text(json.dumps(source))
    (case / "case.yaml").write_text(
        "vars:\n  task: Investigate the consumer\nassert: []\n"
    )
    setup = Path(__file__).parent / "cases/missing-project-dependencies/setup.cjs"
    shutil.copyfile(setup, case / "setup.cjs")
    prepared = tmp_path / "prepared"
    monkeypatch.setattr(prepare, "ROOT", repository)
    monkeypatch.setattr(prepare, "CASES", cases)
    monkeypatch.setattr(prepare, "PREPARED", prepared)
    prepare.prepare()
    config = prepare.YAML_IO.load((prepared / "promptfooconfig.yaml").read_text())
    assert config["tests"][0]["metadata"] == {
        "case": "external",
        "kind": "trajectory",
        "executor_model": "gpt-6-sol",
        "repository_instructions": True,
    }
    for arm in ("historical", "current"):
        workspace = prepared / arm / "external/workspace"
        checkout = workspace / "repository"
        assert git(checkout, "rev-parse", "HEAD") == head
        assert (
            checkout / ".claude/skills/running-tend/SKILL.md"
        ).read_bytes() == overlay.read_bytes()
        assert not (checkout / "future.txt").exists()
        assert not (checkout / "node_modules").exists()
        assert (workspace / ".npm/_cacache").is_dir()
        assert not (workspace / ".npm/_logs").exists()
        assert not list(workspace.rglob("*.log"))
        assert not (workspace / "setup.cjs").exists()
        assert (
            "Its instructions govern repository work"
            in (workspace / "AGENTS.md").read_text()
        )
        provenance = json.loads((workspace.parent / "provenance.json").read_text())
        assert provenance["executor_model"] == "gpt-6-sol"
        assert (
            provenance["setup_sha256"] == hashlib.sha256(setup.read_bytes()).hexdigest()
        )
        attempt = tmp_path / arm / "attempt"
        shutil.copytree(workspace, attempt, symlinks=True)
        settings = json.loads(
            subprocess.run(
                [
                    "node",
                    "-e",
                    "require(process.argv[1])(process.argv[2]).then(x => console.log(JSON.stringify(x)))",
                    str(workspace.parent / "setup.cjs"),
                    str(attempt),
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        for name in ("node", "npm", "git"):
            subprocess.run(
                [name, "--version"],
                env=settings["env"],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["npm", "ci", "--ignore-scripts"],
            cwd=attempt / "repository",
            env=settings["env"],
            check=True,
            capture_output=True,
        )
        assert (
            attempt / "repository/node_modules/fixture/package.json"
        ).read_bytes() == (dependency / "package.json").read_bytes()
        assert not (checkout / "node_modules").exists()
    assert not (external / "node_modules").exists()


@pytest.mark.parametrize("kind", ["history", "fixture"])
def test_legacy_case_kind_is_rejected(repository, tmp_path, monkeypatch, kind):
    cases = tmp_path / "cases"
    case = cases / "legacy"
    case.mkdir(parents=True)
    (case / "source.json").write_text(json.dumps({"kind": kind}))
    monkeypatch.setattr(prepare, "ROOT", repository)
    monkeypatch.setattr(prepare, "CASES", cases)
    monkeypatch.setattr(prepare, "PREPARED", tmp_path / "prepared")
    with pytest.raises(ValueError, match="Unknown case kind"):
        prepare.prepare()
