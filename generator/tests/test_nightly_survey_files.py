"""Tests for nightly_survey_files.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tend.runtime.ci import nightly_survey_files as survey


def test_a_cycle_reviews_each_path_once_in_runs_of_neighbouring_paths() -> None:
    files = [f"{area}/file-{index}.py" for area in "dcba" for index in range(50)]
    unreviewed = [
        "Cargo.lock",
        "tests/snapshots/list.snap",
        "web/__snapshots__/a.js.snap",
        "tests/_regtest_outputs/test_render.test_minimal.out",
    ]
    bucket, selected = survey.survey_files(files + unreviewed, unix_day=10_000)

    assert bucket == 4
    assert selected
    selections = [
        survey.survey_files(files + unreviewed, unix_day=day)[1]
        for day in range(survey.CYCLE_LENGTH)
    ]
    # Read day by day, the cycle walks the reviewable tree once in path order,
    # so each directory's files fall on consecutive days.
    assert [path for paths in selections for path in paths] == sorted(files)


def test_main_lists_the_tracked_files_in_the_fixed_days_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    files = [f"file-{index}.py" for index in range(100)]
    for path in files:
        (tmp_path / path).write_text("\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    assert survey.main(now=10_000 * 86400) == 0

    captured = capsys.readouterr()
    bucket, selected = survey.survey_files(sorted(files), unix_day=10_000)
    assert captured.out.splitlines() == selected
    assert captured.err == f"# bucket={bucket}/28 files={len(selected)}\n"
