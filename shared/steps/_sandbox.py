"""Compose the environment at the single runner-to-SRT boundary.

The outer supervisor crosses the UID boundary once, with exactly this environment
and nothing inherited.
SRT then finalizes proxy variables for its network namespace, and every command
inside the lifecycle inherits that environment unchanged. Trusted preparation
commands that run before SRT use :func:`agent_env` and receive no GitHub
context.

The agent gets the job's own environment, so whatever ``setup:`` exported
crosses without Tend naming it, except :data:`WITHHELD_PREFIXES` (``ACTIONS_*``
is the runner's service channel, ``INPUT_*`` carries an action's inputs) and
:data:`WITHHELD` (the real ``GITHUB_TOKEN``, and the file-command paths through
which a step reaches later ones). The agent env file comes after the job's, so
its proxy routing and dummy credentials win.
"""

from __future__ import annotations

import os
from pathlib import Path

WITHHELD_PREFIXES = ("ACTIONS_", "INPUT_")

WITHHELD = frozenset(
    {
        "GITHUB_TOKEN",
        "GITHUB_ENV",
        "GITHUB_PATH",
        "GITHUB_OUTPUT",
        "GITHUB_STATE",
        "GITHUB_STEP_SUMMARY",
    }
)


def agent_env(agent_env_file: str | os.PathLike[str]) -> list[str]:
    """Read the fixed sandbox environment as ``NAME=VALUE`` arguments."""
    # Split on newlines alone, and read without newline translation: the file
    # holds one assignment per \n, and a \r, \v or U+2028 in a value (the
    # job's PATH among them) belongs to that value. Split anywhere else, one
    # NAME=VALUE becomes two `env` arguments, and `env` execs a trailing
    # argument that is not an assignment as the command to run.
    with Path(agent_env_file).open(encoding="utf-8", newline="") as handle:
        lines = handle.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def launch_env(agent_env_file: str | os.PathLike[str]) -> list[str]:
    """The ``NAME=VALUE`` entries for the outer SRT launch, file last.

    Reads the environment when called, so call it in the step that forwards it.
    """
    lines = agent_env(agent_env_file)
    # A name the file defines stays out of the job half, so the real value a
    # dummy replaces is never written into the launch environment file.
    defined = {line.split("=", 1)[0] for line in lines}
    return [
        f"{name}={value}"
        for name, value in os.environ.items()
        if name not in defined
        and name not in WITHHELD
        and not name.startswith(WITHHELD_PREFIXES)
    ] + lines
