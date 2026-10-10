"""Render the harness-neutral prompt text for one harness.

`shared/system-prompt.md` is the one home for instructions both harnesses read:
the Claude action appends it to the system prompt, and the Codex runner stages
it into `AGENTS.md`. Three things in it vary — the bot's name, merge mode,
and how a skill is invoked — so the file writes those as `${BOT_NAME}`,
`${TEND_MERGE}`, and `${SKILL:<name>}` and this module substitutes them.

"""

from __future__ import annotations

import re

from tend.config import SKILL_PREFIX

SKILL_REF = re.compile(r"\$\{SKILL:([a-z0-9-]+)\}")


def render(text: str, *, bot_name: str, merge: str, harness: str) -> str:
    """Substitute skill references and runtime policy into *text*."""
    prefix = SKILL_PREFIX[harness]
    text = SKILL_REF.sub(lambda match: prefix + match.group(1), text)
    return (
        text.replace("${BOT_NAME}", bot_name)
        .replace("$BOT_NAME", bot_name)
        .replace("${TEND_MERGE}", merge)
        .replace("$TEND_MERGE", merge)
    )
