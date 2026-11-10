"""Report whether the authenticated bot's classic PAT has Tend's scopes."""

from __future__ import annotations

from tend.runtime.shared import _common

REQUIRED = ("repo", "workflow", "notifications", "write:discussion", "gist", "user")


def audit(headers: str) -> dict[str, str]:
    """Parse GitHub response headers into the skill's key-value result."""
    scopes_line = next(
        (
            line.partition(":")[2]
            for line in headers.splitlines()
            if line.casefold().startswith("x-oauth-scopes:")
        ),
        None,
    )
    if scopes_line is None:
        return {"STATUS": "fine-grained"}

    granted = [scope.strip() for scope in scopes_line.split(",") if scope.strip()]
    missing = [scope for scope in REQUIRED if scope not in granted]
    result = {
        "STATUS": "missing" if missing else "ok",
        "GRANTED": ",".join(granted),
        "REQUIRED": ",".join(REQUIRED),
        "MISSING": ",".join(missing),
    }
    return result


def main() -> int:
    result = audit(_common.gh("api", "-i", "user"))
    for key, value in result.items():
        print(f"{key}={value}")
    return 0
