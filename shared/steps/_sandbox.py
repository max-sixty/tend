"""Compose the environment at the single runner-to-SRT boundary.

The outer supervisor crosses the UID boundary once with a clean ``env -i``.
SRT then finalizes proxy variables for its network namespace, and every command
inside the lifecycle inherits that environment unchanged. Trusted preparation
commands that run before SRT use :func:`agent_env` and receive no GitHub
context.

The agent gets the job's own environment, so whatever ``setup:`` exported
crosses without Tend naming it, except :data:`WITHHELD_PREFIXES` (``ACTIONS_*``
is the runner's service channel, ``INPUT_*`` carries an action's inputs) and
:data:`WITHHELD` (the real ``GITHUB_TOKEN``, and the file-command paths through
which a step reaches later ones). The agent env file comes after the job's, so
its proxy routing, dummy credentials, and ``sandbox_env:`` win.
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
    # Split on newlines alone: a carried value may hold a character `str`
    # considers a line break (\v, \f, U+2028) and the file does not.
    # `newline=""` for the same reason, one layer down: the default translates
    # a lone \r to \n before anything here sees it, so a \r in a `sandbox_env:`
    # value — which the config layer does not reject — would split one
    # NAME=VALUE into two `env` arguments, and `env` execs a trailing argument
    # that is not an assignment as the command to run.
    # `surrogateescape` because the shell that wrote the file was byte
    # transparent: a non-UTF-8 byte in a consumer's `sandbox_env:` value must
    # reach the sandbox as it was written, not fail the step before the launch.
    # `subprocess` re-encodes it with `os.fsencode`, which round-trips it back.
    with Path(agent_env_file).open(
        encoding="utf-8", errors="surrogateescape", newline=""
    ) as handle:
        lines = handle.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def launch_env(agent_env_file: str | os.PathLike[str]) -> list[str]:
    """The ``NAME=VALUE`` arguments for the outer SRT launch, file last.

    Reads the environment when called, so call it in the step that forwards it.
    """
    lines = agent_env(agent_env_file)
    # A name the file defines stays out of the job half, so the real value a
    # dummy replaces never reaches the launch argv the parent `sudo` holds.
    defined = {line.split("=", 1)[0] for line in lines}
    return [
        f"{name}={value}"
        for name, value in os.environ.items()
        if name not in defined
        and name not in WITHHELD
        and not name.startswith(WITHHELD_PREFIXES)
    ] + lines
