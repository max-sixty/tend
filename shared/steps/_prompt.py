"""Render the harness-neutral prompt text for one harness.

`shared/system-prompt.md` is the one home for instructions both harnesses read:
the Claude action appends it to the system prompt, and the Codex runner stages
it into `AGENTS.md`. Only two things in it vary — the bot's name, and how a
skill is invoked — so the file writes those as `${BOT_NAME}` and
`${SKILL:<name>}` and this module substitutes both.

`SKILL_PREFIX` restates the mapping `Config.default_prompt` applies to
generated workflow prompts, because the generator is not installed on the
runner and this code is not importable from the generator;
`test_skill_prefixes_match_the_generator` in `generator/tests/test_repo_pins.py`
holds the pair in step.
"""

from __future__ import annotations

import re

SKILL_PREFIX = {"claude": "/tend-ci-runner:", "codex": "$"}

SKILL_REF = re.compile(r"\$\{SKILL:([a-z0-9-]+)\}")


def render(text: str, *, bot_name: str, harness: str) -> str:
    """Substitute skill references and the bot's name into *text*."""
    prefix = SKILL_PREFIX[harness]
    text = SKILL_REF.sub(lambda match: prefix + match.group(1), text)
    return text.replace("${BOT_NAME}", bot_name).replace("$BOT_NAME", bot_name)
