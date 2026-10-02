"""Protect replay boundaries and literal context, without retaining full sessions."""

import hashlib
import json

import prepare
import pytest
from prepare import (
    ORIGINAL_PLUGIN,
    cut_history,
    parse_append_prompt,
    prepare_history,
    stage_fixtures,
)


def test_cutoff_requires_complete_tool_exchange():
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-10-01T00:00:01Z",
            "message": {"content": [{"type": "tool_use", "id": "read-1"}]},
        },
        {
            "type": "user",
            "timestamp": "2026-10-01T00:00:02Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "read-1"}]},
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-01T00:00:03Z",
            "message": {"content": "The original decision"},
        },
    ]
    assert cut_history(rows, "2026-10-01T00:00:03Z") == rows[:2]
    for before, reason in [
        ("2026-10-01T00:00:02Z", "splits a tool exchange"),
        ("2026-10-01T00:00:00Z", "inside the recorded session"),
        ("2026-10-01T00:00:04Z", "inside the recorded session"),
        ("2026-10-01T00:00:03", "include a time zone"),
    ]:
        with pytest.raises(ValueError, match=reason):
            cut_history(rows, before)


def test_actions_prompt_preserves_body_indentation():
    log = (
        "job\tstep\t2026-10-01T00:00:00Z   TEND_SYSTEM_PROMPT: First line\n"
        "job\tstep\t2026-10-01T00:00:00Z \n"
        "job\tstep\t2026-10-01T00:00:00Z     indented code\n"
        "job\tstep\t2026-10-01T00:00:00Z   TEND_PROMPT: next\n"
    )
    assert parse_append_prompt(log) == "First line\n\n    indented code"


def test_current_skill_expands_variables_but_preserves_source_evidence(tmp_path):
    skill = tmp_path / "skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: example\n---\n${CLAUDE_PLUGIN_ROOT}\n$ARGUMENTS\n")
    rows = [
        {"type": "user", "message": {"content": "<command-args>a\nb</command-args>"}},
        {
            "type": "user",
            "message": {
                "content": f"Base directory for this skill: {ORIGINAL_PLUGIN}/skills/example\n\nOld guidance"
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "content": "Inspected old source"}]
            },
        },
    ]
    payload, replacements = prepare_history(
        rows, tmp_path, current=True, expected=["example"]
    )
    prepared = [json.loads(line) for line in payload.splitlines()]
    assert prepared[1]["message"]["content"] == (
        f"Base directory for this skill: {tmp_path}/skills/example\n\n{tmp_path}\na\nb\n"
    )
    assert prepared[2] == rows[2]
    assert replacements[0]["new"] == "example"


def test_fixture_evidence_is_verified_including_cached_logs(tmp_path, monkeypatch):
    case = tmp_path / "case"
    case.mkdir()
    (case / "discussion.json").write_text('{"state":"open"}\n')
    local_hash = hashlib.sha256((case / "discussion.json").read_bytes()).hexdigest()
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
    stage_fixtures(source, case, destination)
    assert (destination / "discussion.json").read_bytes() == (
        case / "discussion.json"
    ).read_bytes()
    assert (destination / "evidence/ci.log").read_bytes() == log_bytes
    cached.write_bytes(b"Different evidence")
    with pytest.raises(ValueError, match="Evidence hash mismatch"):
        stage_fixtures(source, case, destination)


def test_fixture_case_uses_fresh_executors_with_identical_evidence(
    tmp_path, monkeypatch
):
    cases = tmp_path / "cases"
    case = cases / "fixture"
    case.mkdir(parents=True)
    fixture = case / "current.log"
    fixture.write_text("Current diagnostic\n")
    source = {
        "kind": "fixture",
        "historical_ref": "HEAD",
        "bot": "tend-agent",
        "model": "claude-opus-5",
        "fixtures": {"current.log": hashlib.sha256(fixture.read_bytes()).hexdigest()},
    }
    (case / "source.json").write_text(json.dumps(source))
    (case / "case.yaml").write_text(
        "description: Fixture disposition\nvars:\n  task: Read the evidence and write captured.md.\nassert: []\n"
    )
    prepared = tmp_path / "prepared"
    monkeypatch.setattr(prepare, "CASES", cases)
    monkeypatch.setattr(prepare, "PREPARED", prepared)
    prepare.main()
    config = prepare.YAML_IO.load((prepared / "promptfooconfig.yaml").read_text())
    evidence = str(prepared / "fixtures/fixture")
    assert config["tests"][0]["vars"]["evidence_root"] == evidence
    assert config["tests"][0]["providers"] == ["fixture/*"]
    for provider in config["providers"]:
        settings = provider["config"]
        assert "resume" not in settings
        assert settings["additional_directories"][-1] == evidence
        assert "Grep" in settings["tools"]
        assert "Bash" not in settings["tools"]
        assert settings["permission_mode"] == "dontAsk"
        assert settings["settings"]["permissions"][
            "blockReadsOutsideWorkingDirectories"
        ]
    assert (
        prepared / "fixtures/fixture/current.log"
    ).read_text() == fixture.read_text()
