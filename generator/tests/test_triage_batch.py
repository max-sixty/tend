"""Run the generated batch selector: actual IDs, no placeholder workers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from tend.config import Config
from tend.workflows import generate_triage

from tests import BASH, UV, fake_bin, tool_path
from tests._yaml import safe_load


@pytest.mark.parametrize(
    ("numbers", "expected"),
    [
        ([], []),
        ([7], [7]),
        ([7, 7, 8], [7, 8]),
        (list(range(1, 101)), list(range(1, 101))),
    ],
)
def test_rendered_batch_selects_exact_issue_ids(
    tmp_path: Path, numbers, expected
) -> None:
    result, outputs, calls = run_selector(tmp_path, json.dumps(numbers))
    assert result.returncode == 0, result.stderr
    assert json.loads(outputs.removeprefix("issues=")) == expected
    # Deduplication happens before API reads, and empty selections do no work.
    assert len(calls.splitlines()) == len(expected)


@pytest.mark.parametrize(
    "raw",
    [
        '"7"',
        "[true]",
        "[1.2]",
        "[0]",
        "[-1]",
        "{}",
        "null",
        "oops",
        json.dumps(list(range(1, 258))),
    ],
)
def test_invalid_batch_fails_before_starting_jobs(tmp_path: Path, raw: str) -> None:
    result, outputs, calls = run_selector(tmp_path, raw)
    assert result.returncode != 0
    assert outputs == ""
    assert calls == ""


def test_queued_selection_is_revalidated_before_expansion(tmp_path: Path) -> None:
    # An issue closed while waiting, a PR, and a bookkeeping issue never get
    # agent jobs. A CI report remains ordinary issue work.
    records = {
        "1": {"state": "closed", "labels": []},
        "2": {"state": "open", "labels": [], "pull_request": {}},
        "3": {"state": "open", "labels": [{"name": "tend-outage"}]},
        "4": {"state": "open", "labels": [{"name": "tend-ci-failure"}]},
    }
    result, outputs, _ = run_selector(tmp_path, "[1,2,3,4]", records)
    assert result.returncode == 0, result.stderr
    assert outputs == "issues=[4]\n"


def test_unreadable_issue_does_not_silently_lose_selected_work(tmp_path: Path) -> None:
    result, outputs, _ = run_selector(tmp_path, "[7]", {"7": None})
    assert result.returncode != 0
    assert outputs == ""


def run_selector(tmp_path: Path, raw: str, records: dict | None = None):
    config = tmp_path / "tend.yaml"
    config.write_text("bot_name: test-bot\n")
    workflow = safe_load(generate_triage(Config.load(config)).content)
    step = workflow["jobs"]["select"]["steps"][-1]
    responses = tmp_path / "issues.json"
    responses.write_text(json.dumps(records or {}))
    # Use the real rendered shell and script. Only the GitHub transport is fake.
    bindir = fake_bin(
        tmp_path,
        gh="""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with Path(os.environ["GH_CALLS"]).open("a") as output:
    output.write(" ".join(sys.argv[1:]) + "\\n")
number = sys.argv[-1].rsplit("/", 1)[-1]
record = json.loads(Path(os.environ["ISSUE_RECORDS"]).read_text()).get(
    number, {"state": "open", "labels": []})
if record is None:
    sys.exit(1)
print(json.dumps(record))
""",
    )
    output = tmp_path / "output"
    calls = tmp_path / "calls"
    result = subprocess.run(
        [BASH, "-e", "-c", step["run"]],
        env={
            **os.environ,
            "PATH": tool_path(bindir),
            "TEND_UV": UV,
            "ISSUES": raw,
            "BOOKKEEPING_LABELS": step["env"]["BOOKKEEPING_LABELS"],
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "owner/repo",
            "ISSUE_RECORDS": str(responses),
            "GH_CALLS": str(calls),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result,
        output.read_text() if output.exists() else "",
        calls.read_text() if calls.exists() else "",
    )
