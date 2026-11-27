"""Run the agent process under the harness's time bound.

Both harnesses launch their agent through :func:`supervise`, and both export
:func:`deadline` to it as ``TEND_DEADLINE``, so a foreground wait such as a CI
poll can hand back before the bound stops the run.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tend.runtime.shared import _common

#: Seconds the agent has to flush after the bound's TERM, before the KILL.
TERM_GRACE_SEC = 5


def deadline(timeout_sec: int) -> int:
    """When a run started now is stopped by the bound, in epoch seconds."""
    return int(time.time()) + timeout_sec


@dataclass(frozen=True)
class Supervised:
    """How the supervised launch ended."""

    #: The agent's exit code, or None when the bound killed the run.
    exit_code: int | None
    elapsed: int


def supervise(
    argv: list[str],
    *,
    timeout_sec: int,
    stdout: Path | None = None,
    stderr: Path | None = None,
) -> Supervised:
    """Run *argv* under the bound, its streams to *stdout*/*stderr* if given.

    A stream given no file is inherited from this process.

    stdin is closed explicitly. A backgrounded command got ``/dev/null`` for
    free; this one is in the foreground and would inherit the step's, which
    costs ``claude -p`` a three-second wait for input that never comes and feeds
    it whatever does arrive.

    Overrunning the bound asks the agent to stop before making it: a TERM to the
    agent process group, :data:`TERM_GRACE_SEC` to flush, then the KILL. Without
    the grace the agent's session log loses its tail mid-write, and the runs
    that time out are exactly the ones whose log is read to find out why.

    The KILL is this function's ``finally`` and the only unconditional step: it
    is what actually stops a run the TERM did not, so no path out of here,
    exception included, may skip it. :func:`_common.raise_on_cancel` is what makes
    "every path" include a cancelled job, which arrives as a signal rather than
    as anything Python would raise on its own.

    The KILL is followed by a wait on the child, so this inner lifecycle does
    not return while a descendant still holds its stream descriptors. The
    outer supervisor then reaps the entire sandbox uid.
    """
    start = time.monotonic()
    agent: subprocess.Popen[bytes] | None = None
    try:
        with contextlib.ExitStack() as streams:
            streams.enter_context(_common.raise_on_cancel())
            out = streams.enter_context(stdout.open("wb")) if stdout else None
            err = streams.enter_context(stderr.open("wb")) if stderr else None
            agent = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            try:
                returncode = agent.wait(timeout_sec)
                # Shell convention: 128 + N when signal N killed the child.
                code = 128 - returncode if returncode < 0 else returncode
            except subprocess.TimeoutExpired:
                # The bound decides, however the stop then goes: an agent that
                # takes the TERM and one that has to be killed are the same run
                # to a maintainer, and the code it leaves says nothing.
                code = None
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(agent.pid, signal.SIGTERM)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    agent.wait(TERM_GRACE_SEC)
    finally:
        if agent is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(agent.pid, signal.SIGKILL)
            agent.wait()
    return Supervised(code, round(time.monotonic() - start))
