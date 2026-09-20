"""Compose the environment at the single runner-to-SRT boundary.

The outer supervisor crosses the UID boundary once with a clean ``env -i``.
SRT then finalizes proxy variables for its network namespace, and every command
inside the lifecycle inherits that environment unchanged. Trusted preparation
commands that run before SRT use :func:`agent_env` and receive no GitHub
context.

The agent runs in the job's own home and checkout, so it gets the job's own
environment too: whatever a consumer's ``setup:`` steps exported — ``JAVA_HOME``,
``DOTNET_ROOT``, ``PNPM_HOME``, ``GOROOT``, ``CARGO_INCREMENTAL`` — crosses
without Tend naming any of them. That is what stops this becoming a list a
consumer has to complete before their toolchain works, and it is why the
exclusions below are GitHub-defined namespaces rather than Tend's judgement
about which variables matter.

What does not cross, and why:

- :data:`WITHHELD_PREFIXES` — ``ACTIONS_*`` is the runner's own service
  channel (``ACTIONS_RUNTIME_TOKEN``, the cache and results URLs), and
  ``INPUT_*`` is how an action's inputs, some of them secrets, reach a step.
- :data:`WITHHELD` — ``GITHUB_TOKEN``, whose real value must never leave the
  proxy, and the five file-command paths the runner re-reads after the step
  exits. The sandbox must not be handed a channel into a later step's
  environment, PATH, outputs, state, or job summary. The view masks the
  directory those paths live in as well; that stops reads of what earlier steps
  wrote, where withholding the names stops writes to what later steps read.
- Every name the agent environment file already defines. The file is the half
  that routes the sandbox through the proxy and hands it dummy credentials, so
  the job's ambient ``HOME``, ``PATH`` or ``HTTPS_PROXY`` must not land on top
  of it.

Order follows from that last point: the job context first, the file second, so
the file wins wherever it sets anything. A consumer's ``sandbox_env:`` is part
of the file, which is what makes it an override of the job environment rather
than a suggestion — and is why ``setup_sandbox.py`` refuses a ``GITHUB_*`` name
there, so the context a run reads about itself stays the runner's.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Namespaces GitHub defines and the sandbox must not receive.
WITHHELD_PREFIXES = ("ACTIONS_", "INPUT_")

#: The individual names that must not cross the uid boundary.
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
    """The ``NAME=VALUE`` arguments for the outer SRT launch.

    The job's environment first, then the file's lines (proxy routing, CA
    trust, dummy credentials, and the consumer's own ``sandbox_env:``
    additions), which is the order the module docstring explains.

    A caller may append names of its own afterwards — they win, which is what
    Tend's own staged-bundle paths want — provided none is a key the file
    defines, which would put the sandbox's routing back in play.

    Reads the environment when called, so call it in the step that forwards it.
    """
    lines = agent_env(agent_env_file)
    defined = {line.split("=", 1)[0] for line in lines if "=" in line}
    return [
        f"{name}={value}"
        for name, value in os.environ.items()
        if name not in defined
        and name not in WITHHELD
        and not name.startswith(WITHHELD_PREFIXES)
    ] + lines
