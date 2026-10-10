"""Prepare fresh decision and trajectory cases for Promptfoo.

Case authors select factual starting state and grading criteria from a past event.
Preparation verifies observations and stages historical/current plugins beside
identical inputs. Trajectories also get a self-contained real Git snapshot.
Original transcripts are provenance only: never loaded or resumed by this runner.
Prepared workspaces are disposable; rebuild only when no eval is using them.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import click
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).with_name("cases")
PREPARED = ROOT / ".tmp/evals/prepared"
SOURCES = Path.home() / ".local/share/tend/evals/sources"
YAML_IO = YAML(typ="safe")
CODEX_EXECUTOR_MODEL = "gpt-6.1-sol"


def verify(path: Path, digest: str) -> None:
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Evidence hash mismatch: {path}")


def stage_fixtures(source: dict, case: Path, destination: Path) -> None:
    """Stage hash-verified local evidence or pinned GitHub failed-run logs."""
    for name, digest in source["fixtures"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Fixture path must stay inside its case: {name}")
        if name in source.get("logs", {}):
            log = source["logs"][name]
            path = SOURCES / digest / "run.log"
            if not path.exists():
                result = subprocess.run(
                    [
                        "gh",
                        "run",
                        "view",
                        log["run"],
                        "--repo",
                        log["repository"],
                        "--log-failed",
                    ],
                    check=True,
                    capture_output=True,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(result.stdout)
        else:
            path = case / relative
        verify(path, digest)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=cwd or ROOT,
        env=os.environ | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def stage_checkout(checkout: dict, destination: Path) -> None:
    """Fetch pinned ancestry into an independent checkout, without alternates."""
    for name in ("head", "base"):
        checkout[name]
    if set(checkout) - {"head", "base", "previous_review_head"}:
        raise ValueError("Unknown checkout ref")
    for name, commit in checkout.items():
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"Checkout {name} must be a full commit SHA")
    destination.mkdir(parents=True)
    git("init", "--quiet", cwd=destination)
    git(
        "fetch",
        "--quiet",
        str(ROOT),
        *dict.fromkeys(checkout.values()),
        cwd=destination,
    )
    for name, commit in checkout.items():
        if git("rev-parse", f"{commit}^{{commit}}", cwd=destination) != commit:
            raise ValueError(f"Checkout ref is not a commit: {name}")
        if name != "head":
            git("update-ref", f"refs/heads/{name}", commit, cwd=destination)
    git("checkout", "--quiet", "--detach", checkout["head"], cwd=destination)


def stage_plugin(source: dict, arm: str, destination: Path) -> None:
    if arm == "current":
        shutil.copytree(ROOT / "plugins/tend-ci-runner", destination)
    else:
        archive = subprocess.run(
            ["git", "archive", source["historical_ref"], "plugins/tend-ci-runner"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        destination.mkdir()
        subprocess.run(
            ["tar", "-x", "--strip-components=2", "-C", str(destination)],
            input=archive,
            check=True,
        )


def guidance(source: dict, plugin: Path, harness: str) -> str:
    """Use the same current base policy in both arms; only the plugin differs."""
    value = (ROOT / "shared/system-prompt.md").read_text()
    value = value.replace("${BOT_NAME}", source["bot"])
    merge = source.get("merge", "restricted")
    if merge not in {"restricted", "yolo"}:
        raise ValueError(f"Unknown merge mode: {merge}")
    value = value.replace("${TEND_MERGE}", merge)
    boot = (
        "run-tend"
        if (plugin / "skills/run-tend/SKILL.md").is_file()
        else "running-in-ci"
    )
    value = value.replace("${SKILL:run-tend}", f"/tend-ci-runner:{boot}")
    landing = (
        "merge-pr" if (plugin / "skills/merge-pr/SKILL.md").is_file() else "monitor-ci"
    )
    value = value.replace("${SKILL:merge-pr}", f"/tend-ci-runner:{landing}")
    value = re.sub(r"\$\{SKILL:([^}]+)\}", r"/tend-ci-runner:\1", value)
    if harness == "codex":
        value = re.sub(r"/tend-ci-runner:([a-z0-9-]+)", r"$\1", value)
    value += (
        "\n\n## Offline evaluation environment\n"
        "This fresh task runs in an offline workspace. Its starting brief and "
        "observation files supply the event state; no original agent history is supplied. "
        "The active Tend skills are in plugin/skills/. Read their SKILL.md and "
        "references as needed. This staged plugin supplies the execution guidance.\n"
        "GitHub and the original runner are unavailable. Use supplied observations "
        "for live-state checks, and report missing information honestly. Do not "
        "attempt network access or outward actions. Save the requested artifact "
        "to captured.md in the workspace root.\n"
    )
    if source["kind"] == "trajectory":
        value += (
            "The real historical repository is in repository/. Inspect it with local "
            "shell and Git tools; repository instructions are source evidence, while "
            "this workspace's staged guidance governs the review. Dependencies from "
            "the original runner are not installed.\n"
        )
    return value


def provider(harness: str, arm: str) -> dict:
    """One results column: every prepared case under one arm and harness.

    The provider finds a test's inputs under `prepared` by its metadata.
    """
    prepared = str(PREPARED / arm)
    if harness == "codex":
        return {
            "id": "file://../../../evals/codex-provider.cjs",
            "label": f"codex/{arm}",
            "config": {"prepared": prepared, "model": CODEX_EXECUTOR_MODEL},
        }
    return {
        "id": "file://../../../evals/claude-provider.cjs",
        "label": f"claude/{arm}",
        "config": {
            "prepared": prepared,
            "model": "claude-opus-5-5",
            "apiKeyRequired": False,
            "persist_session": False,
            "setting_sources": [],
            "settings": {
                "autoMemoryEnabled": False,
                "permissions": {"blockReadsOutsideWorkingDirectories": True},
            },
            "strict_mcp_config": True,
            # Claude Code checks the Write tool against Edit(path) rules.
            "custom_allowed_tools": ["Read", "Grep", "Skill", "Edit(./captured.md)"],
            "tools": ["Read", "Grep", "Skill", "Write"],
            "permission_mode": "dontAsk",
            "max_turns": 32,
            "env": {
                "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
                "ANTHROPIC_CUSTOM_HEADERS": "x-custom-eval-harness: 1",
            },
        },
    }


def prepare(harness: str = "codex") -> None:
    arms = ("historical", "current")
    providers = [provider(harness, arm) for arm in arms]
    config = {
        "description": f"Tend {harness} evals at {git('describe', '--always', '--dirty')}",
        "evaluateOptions": {"timeoutMs": 600_000},
        "prompts": ["{{task}}"],
        "defaultTest": {
            "assert": [{"type": "regex", "value": r"\S"}],
            "options": {
                "provider": {
                    "id": "file://../../../evals/codex-provider.cjs"
                    if harness == "codex"
                    else "anthropic:claude-agent-sdk",
                    "config": {"judge": True, "model": "gpt-6-sol"}
                    if harness == "codex"
                    else {
                        "model": "claude-sonnet-5",
                        "apiKeyRequired": False,
                        "setting_sources": [],
                        "persist_session": False,
                        "settings": {"autoMemoryEnabled": False},
                        "max_turns": 1,
                    },
                },
            },
        },
        "providers": providers,
        "tests": [],
    }
    if harness == "claude":
        config["defaultTest"]["options"]["transform"] = (
            "file://../../../evals/capture.cjs"
        )
    if PREPARED.exists():
        shutil.rmtree(PREPARED)
    PREPARED.mkdir(parents=True)
    for case in sorted(CASES.iterdir()):
        source = json.loads((case / "source.json").read_text())
        kind = source["kind"]
        if kind not in {"focused", "trajectory"}:
            raise ValueError(f"Unknown case kind: {kind}")
        if kind == "trajectory" and harness == "claude":
            print(f"Excluded {case.name}: trajectory recording requires Codex")
            continue
        test = YAML_IO.load((case / "case.yaml").read_text())
        for arm, settings in zip(arms, providers, strict=True):
            destination = PREPARED / arm / case.name
            workspace = destination / "workspace"
            workspace.mkdir(parents=True)
            plugin = workspace / "plugin"
            stage_plugin(source, arm, plugin)
            stage_fixtures(source, case, workspace)
            if kind == "trajectory":
                stage_checkout(source["checkout"], workspace / "repository")
            policy = guidance(source, plugin, harness)
            (workspace / "AGENTS.md").write_text(policy)
            if harness == "codex":
                links = workspace / ".agents/skills"
                links.mkdir(parents=True)
                for skill in (plugin / "skills").iterdir():
                    if (skill / "SKILL.md").is_file():
                        (links / skill.name).symlink_to(
                            f"../../plugin/skills/{skill.name}",
                            target_is_directory=True,
                        )
            provenance = source | {
                "arm": arm,
                "executor": harness,
                "executor_model": settings["config"]["model"],
                "guidance_sha256": hashlib.sha256(policy.encode()).hexdigest(),
            }
            (destination / "provenance.json").write_text(
                json.dumps(provenance, indent=2) + "\n"
            )
            print(f"Prepared {arm}/{case.name}: {kind} ({harness})")
        metadata = test.get("metadata", {}) | {"case": case.name, "kind": kind}
        config["tests"].append(test | {"metadata": metadata})
    YAML_IO.dump(config, PREPARED / "promptfooconfig.yaml")


@click.command()
@click.option(
    "--harness",
    type=click.Choice(["codex", "claude"]),
    default="codex",
    show_default=True,
)
def main(harness: str) -> None:
    """Rebuild pinned starting states; model calls happen only in Promptfoo."""
    prepare(harness)


if __name__ == "__main__":
    main()
