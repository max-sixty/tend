"""Small workflow operations whose decisions must be testable outside YAML.

Manifests supply event fields and credentials; these operations own timestamp
arithmetic, live-head comparison, and the bot's acknowledgment lifecycle.
"""

from __future__ import annotations

import os
from datetime import datetime

from tend.runtime.shared import _common


def queue_delay() -> int:
    timestamp = os.environ.get("EVENT_TS", "")
    seconds = ""
    if timestamp:
        event_epoch = int(datetime.fromisoformat(timestamp).timestamp())
        seconds = str(int(os.environ["TEND_JOB_STARTED_AT"]) - event_epoch)
    _common.set_output("seconds", seconds)
    return 0


def current_head() -> int:
    env = _common.require_env("GITHUB_REPOSITORY", "PR", "SHA")
    pr = _common.gh_json("api", f"repos/{env['GITHUB_REPOSITORY']}/pulls/{env['PR']}")
    _common.set_output("current", str(pr["head"]["sha"] == env["SHA"]).lower())
    return 0


def reaction(*, remove: bool) -> int:
    env = _common.require_env("REPO", "TARGET")
    endpoint = f"repos/{env['REPO']}/{env['TARGET']}/reactions"
    try:
        if remove:
            bot = _common.require_env("BOT_NAME")["BOT_NAME"]
            pages = _common.gh_json(
                "api", "--paginate", "--slurp", f"{endpoint}?content=eyes&per_page=100"
            )
            for page in pages:
                for item in page:
                    if item["user"]["login"] == bot:
                        _common.gh(
                            "api",
                            "-X",
                            "DELETE",
                            f"{endpoint}/{item['id']}",
                            "--silent",
                        )
                        return 0
        else:
            _common.gh("api", endpoint, "-f", "content=eyes", "--silent")
    except _common.GH_READ_FAILED:
        action = "remove" if remove else "add"
        print(f"::warning::could not {action} the eyes reaction")
    return 0
