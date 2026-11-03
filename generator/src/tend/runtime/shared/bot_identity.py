"""Resolve the credential's identity without gating work on creation volume."""

from tend.runtime.shared import _common


def read() -> tuple[str, int]:
    identity = _common.gh_json("api", "user")
    login, number = identity["login"], identity["id"]
    if not isinstance(login, str) or not login or type(number) is not int:
        raise _common.UnexpectedShape("invalid authenticated identity")
    return login, number


def main() -> int:
    login, number = read()
    _common.set_output("login", login)
    _common.set_output("id", str(number))
    return 0
