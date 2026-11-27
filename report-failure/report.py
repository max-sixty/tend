"""Run the outage reporter from this action's source, with no installed runtime.

The reporter and the modules it imports use only the standard library, so the
source tree beside this action is importable under a bare system Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator" / "src"))

from tend.runtime.shared import _common, report_failure

if __name__ == "__main__":
    _common.run(lambda: report_failure.main(name_failed_step=True))
