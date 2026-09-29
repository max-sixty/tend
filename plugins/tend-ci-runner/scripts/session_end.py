"""When a script's wait must hand control back to the tend session.

Each harness exports ``TEND_DEADLINE``, the epoch second at which the session
is stopped. A wait that outlives it ends the session mid-command: nothing
reports what the wait was for, and the run reads as an outage.
"""

from __future__ import annotations

import math
import os

#: How long before the session's end a wait returns, so the session can still
#: report what it could not verify.
REPORT_SEC = 10 * 60


def wait_until() -> float:
    """The epoch second a wait must end by; unbounded outside a tend session."""
    deadline = os.environ.get("TEND_DEADLINE")
    return math.inf if deadline is None else float(deadline) - REPORT_SEC
