"""Protect replay boundaries and literal context, without retaining full sessions."""

import json

import pytest
from prepare import ORIGINAL_PLUGIN, cut_history, parse_append_prompt, prepare_history


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
