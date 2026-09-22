"""Compose the Claude harness's system prompt and emit it as a step output."""

from __future__ import annotations

import os
from pathlib import Path

import _common
import _prompt


def main() -> int:
    shared = Path(os.environ["SYSTEM_PROMPT_FILE"]).read_text()
    base = _prompt.render(
        shared,
        bot_name=os.environ["BOT_NAME"],
        merge=os.environ["TEND_MERGE"],
        harness="claude",
    ).rstrip("\n")
    parts = [base]
    extra = os.environ.get("EXTRA", "")
    if extra:
        parts.append(extra)
    _common.set_output("value", "\n\n".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
