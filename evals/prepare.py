"""Prepare historical decision replays for Promptfoo’s Claude Agent SDK provider.

Preparation owns evidence download, transcript cutoffs and guidance replacement;
Promptfoo executes Claude Code and grades its written PR body. Inputs are
hash-verified; original bad answers stay outside every retained history.
Prepared plugins are disposable; rebuild before a comparison, never while an
eval is using them.
Sources are cached outside the worktree, keyed by transcript hash.

Prototype: future case authors may need to refine prompt extraction and skill
replacement for other session formats. Only injected execution guidance changes;
instruction files read as source evidence remain part of the investigation.
"""

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).with_name("cases")
PREPARED = ROOT / ".tmp/evals/prepared"
SOURCES = Path.home() / ".local/share/tend/evals/sources"
ORIGINAL_PLUGIN = "/home/tend-sandbox/tend-marketplace/plugins/tend-ci-runner"
YAML_IO = YAML(typ="safe")


def verify(path: Path, digest: str) -> None:
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Evidence hash mismatch: {path}")


def fetch(source: dict) -> Path:
    directory = SOURCES / source["sha256"]
    transcript = directory / source["transcript"]
    if not transcript.exists():
        subprocess.run(
            [
                "gh",
                "run",
                "download",
                source["run"],
                "--repo",
                source["repository"],
                "--name",
                source["artifact"],
                "--dir",
                str(directory),
            ],
            check=True,
        )
    verify(transcript, source["sha256"])
    return transcript


def append_prompt(source: dict) -> str:
    if "append_sha256" in source:
        path = SOURCES / source["sha256"] / "append-system-prompt.txt"
        if not path.exists():
            log = subprocess.run(
                [
                    "gh",
                    "run",
                    "view",
                    source["run"],
                    "--repo",
                    source["repository"],
                    "--log",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(parse_append_prompt(log))
        verify(path, source["append_sha256"])
        return path.read_text()
    value = subprocess.run(
        ["git", "show", f"{source['historical_ref']}:shared/system-prompt.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    value = value.replace("${BOT_NAME}", source["bot"])
    return re.sub(r"\$\{SKILL:([^}]+)\}", r"/tend-ci-runner:\1", value)


def parse_append_prompt(log: str) -> str:
    """Recover multiline prompt bytes; Actions indents the environment marker."""
    lines = [
        re.sub(r"^\d{4}-\S+Z[ \t]", "", line.split("\t", 2)[-1])
        for line in log.splitlines()
    ]
    start = next(
        i
        for i, line in enumerate(lines)
        if line.lstrip().startswith("TEND_SYSTEM_PROMPT: ")
    )
    end = next(
        i
        for i in range(start + 1, len(lines))
        if lines[i].lstrip().startswith("TEND_PROMPT: ")
    )
    return "\n".join(
        [lines[start].lstrip().split(": ", 1)[1], *lines[start + 1 : end]]
    ).rstrip("\n")


def message_blocks(entry: dict) -> list[dict]:
    content = entry["message"]["content"]
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def cut_history(entries: list[dict], before: str) -> list[dict]:
    """Retain a physical prefix ending between completed tool exchanges."""
    cutoff = datetime.fromisoformat(before)
    if cutoff.tzinfo is None:
        raise ValueError("Cutoff must include a time zone")
    boundary = next(
        (
            i
            for i, entry in enumerate(entries)
            if "timestamp" in entry
            and datetime.fromisoformat(entry["timestamp"]) >= cutoff
        ),
        len(entries),
    )
    if boundary == 0 or boundary == len(entries):
        raise ValueError("Cutoff must fall inside the recorded session")
    prefix = entries[:boundary]
    pending = set()
    for entry in prefix:
        if entry["type"] not in {"assistant", "user"}:
            continue
        for block in message_blocks(entry):
            if block["type"] == "tool_use":
                pending.add(block["id"])
            elif block["type"] == "tool_result":
                pending.discard(block["tool_use_id"])
    if pending:
        raise ValueError(
            "Cutoff splits a tool exchange; choose a time after its results"
        )
    return prefix


def prepare_history(
    entries: list[dict], plugin: Path, *, current: bool, expected: list[str]
) -> tuple[str, list[dict]]:
    """Replace all injected skill bodies in the current arm; old bodies stay verbatim."""
    entries = json.loads(json.dumps(entries))
    text = "\n".join(
        block["text"]
        for entry in entries
        if entry["type"] == "user"
        for block in message_blocks(entry)
        if block["type"] == "text"
    )
    arguments = re.search(r"<command-args>(.*?)</command-args>", text, re.DOTALL)
    arguments = arguments[1] if arguments else ""
    found = []
    replaced = []
    for entry in entries:
        if entry["type"] != "user":
            continue
        content = entry["message"]["content"]
        blocks = message_blocks(entry)
        for block in blocks:
            if block.get("type") != "text":
                continue
            match = re.match(
                r"Base directory for this skill: "
                + re.escape(ORIGINAL_PLUGIN)
                + r"/skills/([^\n]+)\n",
                block["text"],
            )
            if not match:
                continue
            name = match[1]
            found.append(name)
            if current:
                target = "run-tend" if name == "running-in-ci" else name
                body = (plugin / "skills" / target / "SKILL.md").read_text()
                body = (
                    body.split("---", 2)[2].lstrip("\n")
                    if body.startswith("---\n")
                    else body
                )
                body = body.replace("$ARGUMENTS", arguments)
                body = body.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin))
                block["text"] = (
                    f"Base directory for this skill: {plugin}/skills/{target}\n\n{body}"
                )
                replaced.append(
                    {
                        "old": name,
                        "new": target,
                        "sha256": hashlib.sha256(body.encode()).hexdigest(),
                    }
                )
        if isinstance(content, str):
            entry["message"]["content"] = blocks[0]["text"]
    if found != expected:
        raise ValueError(f"Unexpected injected skills: {found}; expected {expected}")
    payload = "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries)
    payload = payload.replace(ORIGINAL_PLUGIN, str(plugin))
    if current:
        payload = payload.replace(
            '"skill": "tend-ci-runner:running-in-ci"',
            '"skill": "tend-ci-runner:run-tend"',
        )
    return payload + "\n", replaced


def main() -> None:
    config = {
        "description": "Tend production regressions: historical versus current guidance",
        "evaluateOptions": {"timeoutMs": 600_000},
        "prompts": ["{{task}}"],
        "defaultTest": {
            "assert": [{"type": "regex", "value": r"\S"}],
            "options": {
                "transform": "file://../../../evals/capture.cjs",
                "provider": {
                    "id": "anthropic:claude-agent-sdk",
                    "config": {
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
        "providers": [],
        "tests": [],
    }
    for case in sorted(CASES.iterdir()):
        source = json.loads((case / "source.json").read_text())
        transcript = fetch(source)
        entries = [json.loads(line) for line in transcript.read_text().splitlines()]
        prefix = cut_history(entries, source["before"])
        prompt = append_prompt(source)
        test = YAML_IO.load((case / "case.yaml").read_text())
        for arm in ("historical", "current"):
            destination = PREPARED / arm / case.name
            staged_plugin = destination / "plugin"
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir(parents=True)
            if arm == "current":
                shutil.copytree(ROOT / "plugins/tend-ci-runner", staged_plugin)
            else:
                archive = subprocess.run(
                    [
                        "git",
                        "archive",
                        source["historical_ref"],
                        "plugins/tend-ci-runner",
                    ],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                ).stdout
                staged_plugin.mkdir()
                subprocess.run(
                    ["tar", "-x", "--strip-components=2", "-C", str(staged_plugin)],
                    input=archive,
                    check=True,
                )
            payload, replaced = prepare_history(
                prefix,
                staged_plugin,
                current=arm == "current",
                expected=source["loaded_skills"],
            )
            history = destination / "history.jsonl"
            history.write_text(payload)
            guidance = (
                prompt.replace(
                    "/tend-ci-runner:running-in-ci", "/tend-ci-runner:run-tend"
                )
                if arm == "current"
                else prompt
            )
            label = f"{case.name}/{arm}"
            config["providers"].append(
                {
                    "id": "anthropic:claude-agent-sdk",
                    "label": label,
                    "config": {
                        "model": source["model"],
                        "apiKeyRequired": False,
                        "resume": str(history),
                        "fork_session": True,
                        "persist_session": False,
                        "setting_sources": [],
                        "plugins": [{"type": "local", "path": str(staged_plugin)}],
                        "additional_directories": [str(staged_plugin)],
                        "settings": {
                            "autoMemoryEnabled": False,
                            "permissions": {
                                "blockReadsOutsideWorkingDirectories": True
                            },
                        },
                        "strict_mcp_config": True,
                        "append_system_prompt": guidance,
                        "custom_allowed_tools": [
                            "Read",
                            "Skill",
                            "Edit(./captured.md)",
                        ],
                        "tools": ["Read", "Skill", "Write"],
                        "permission_mode": "dontAsk",
                        "max_turns": 12,
                        "env": {
                            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
                        },
                    },
                }
            )
            provenance = source | {
                "arm": arm,
                "retained_rows": len(prefix),
                "excluded_rows": len(entries) - len(prefix),
                "history_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "replaced_skills": replaced,
                "append_sha256": hashlib.sha256(guidance.encode()).hexdigest(),
            }
            (destination / "provenance.json").write_text(
                json.dumps(provenance, indent=2) + "\n"
            )
            print(
                f"Prepared {arm}/{case.name}: {len(prefix)}/{len(entries)} retained rows, {len(replaced)} skill replacements"
            )
        config["tests"].append(test | {"providers": [f"{case.name}/*"]})
    YAML_IO.dump(config, PREPARED / "promptfooconfig.yaml")


if __name__ == "__main__":
    main()
