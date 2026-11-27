"""Release version parsing, kept free of third-party imports.

The outage reporter imports this from the action's own source with no
installed runtime (``report-failure/report.py``), so it must load under a bare
system Python.
"""

from __future__ import annotations


def release(text: str) -> tuple[int, ...] | None:
    """*text* as a release version, or ``None`` for anything that is not one.

    Strict ``X.Y.Z`` — the one form the generator stamps and pins and a tend
    release tags. A ref this cannot parse stays unordered rather than guessed
    at, because a guess that lands the wrong way round tells a consumer on the
    newest release to regenerate.

    ``isdecimal`` rather than ``isdigit``: the latter admits 128 characters
    ``int`` rejects (``²``, ``፩``), so the guard would pass and the parse
    would raise.
    """
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(part.isdecimal() for part in parts):
        return None
    return tuple(int(part) for part in parts)
