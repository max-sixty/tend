"""The environment the sandbox user is launched with: the job's, minus the
withheld names, with the agent env file after it."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import _sandbox
import pytest

WITHHELD = (
    "GITHUB_TOKEN",
    "GITHUB_ENV",
    "GITHUB_PATH",
    "GITHUB_OUTPUT",
    "GITHUB_STATE",
    "GITHUB_STEP_SUMMARY",
)

Compose = Callable[..., list[str]]


@pytest.fixture
def compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Compose:
    """`launch_env` given exactly *env* as the job environment, and *env_file*."""

    def build(env: dict[str, str], env_file: str = "") -> list[str]:
        for name in list(os.environ):
            monkeypatch.delenv(name)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        path = tmp_path / "agent-env"
        path.write_text(env_file)
        return _sandbox.launch_env(path)

    return build


def test_launch_env_puts_the_file_after_the_job(compose: Compose) -> None:
    pairs = compose(
        {"GITHUB_WORKFLOW": "tend-weekly", "CARGO_INCREMENTAL": "1"},
        env_file="HOME=/sandbox\nCARGO_INCREMENTAL=0\n",
    )

    assert pairs == [
        "GITHUB_WORKFLOW=tend-weekly",
        "HOME=/sandbox",
        "CARGO_INCREMENTAL=0",
    ]


def test_launch_env_withholds_every_denied_name(compose: Compose) -> None:
    """Each of the six is dropped, and dropping it is the only thing that is."""
    carried = {
        "GITHUB_WORKFLOW": "tend-weekly",
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_REPOSITORY": "max-sixty/tend",
    }

    pairs = compose({**carried, **{name: f"secret-{name}" for name in WITHHELD}})

    assert sorted(pairs) == sorted(f"{k}={v}" for k, v in carried.items())


def test_launch_env_withholds_the_runner_and_action_namespaces(
    compose: Compose,
) -> None:
    pairs = compose(
        {
            "ACTIONS_RUNTIME_TOKEN": "runner-service-token",
            "ACTIONS_CACHE_URL": "https://cache.invalid/",
            "INPUT_GITHUB_TOKEN": "the-real-pat",
            "GITHUB_ACTOR": "someone",
        }
    )

    assert pairs == ["GITHUB_ACTOR=someone"]


def test_launch_env_carries_what_the_job_exported(compose: Compose) -> None:
    pairs = compose(
        {
            "JAVA_HOME": "/usr/lib/jvm/temurin-21",
            "PNPM_HOME": "/home/runner/setup-pnpm",
            "GITHUB_A_NAME_FROM_2030": "value",
        }
    )

    assert sorted(pairs) == [
        "GITHUB_A_NAME_FROM_2030=value",
        "JAVA_HOME=/usr/lib/jvm/temurin-21",
        "PNPM_HOME=/home/runner/setup-pnpm",
    ]


def test_launch_env_withholding_is_anchored_to_the_prefix(compose: Compose) -> None:
    pairs = compose(
        {
            "MY_ACTIONS_FLAG": "keep",
            "REINPUT_MODE": "keep",
            "ACTIONS_STEP_DEBUG": "drop",
        }
    )

    assert sorted(pairs) == ["MY_ACTIONS_FLAG=keep", "REINPUT_MODE=keep"]


def test_launch_env_reads_the_file_as_the_shell_wrote_it(compose: Compose) -> None:
    """One element per newline-delimited record, and no phantom trailing one.

    `str.splitlines` would also break on \\v, \\f and U+2028 — characters a
    carried value may hold and the file's own framing does not — turning one
    assignment into two arguments, the second of them junk. Universal-newline
    translation on the read does the same for `\\r`, which `sandbox_env:` does
    not reject: `env` runs a trailing argument that is not an assignment as the
    command, so the split turns a stray carriage return into an exec.
    """
    assert compose({}, env_file="A=1\nB=two\vlines\n") == ["A=1", "B=two\vlines"]
    assert compose({}, env_file="FOO=a\rb\n") == ["FOO=a\rb"]
    assert compose({}, env_file="") == []


def test_launch_env_carries_a_value_that_is_not_utf_8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shell that wrote the file was byte transparent, so this must be too.

    A non-UTF-8 byte in a consumer's `sandbox_env:` value would otherwise fail
    the step before the launch. Asserted through a real subprocess, because the
    round trip is `subprocess`'s `os.fsencode`, not anything this module does.
    """
    for name in list(os.environ):
        monkeypatch.delenv(name)
    path = tmp_path / "agent-env"
    path.write_bytes(b"TEND_X=raw\xe9byte\n")

    pairs = _sandbox.launch_env(path)

    echo = "import os, sys; sys.stdout.buffer.write(os.fsencode(sys.argv[1]))"
    echoed = subprocess.run(
        [sys.executable, "-c", echo, pairs[0]], capture_output=True, check=True
    )
    assert echoed.stdout == b"TEND_X=raw\xe9byte"
