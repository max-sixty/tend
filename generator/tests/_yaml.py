"""ruamel.yaml shim with a pyyaml-style ``safe_load`` / ``safe_dump`` surface.

Tests import this as ``yaml`` so the existing ``yaml.safe_load(text)`` /
``yaml.safe_dump(obj)`` call sites keep working under ruamel.yaml's YAML 1.2
semantics (no `on:` → True trap, no Norway problem).
"""

from __future__ import annotations

import io

from ruamel.yaml import YAML

_yaml = YAML(typ="safe", pure=True)
_yaml.default_flow_style = False
_yaml.allow_unicode = True


def safe_load(text: str) -> object:
    return _yaml.load(text)


def safe_dump(obj: object) -> str:
    buf = io.StringIO()
    _yaml.dump(obj, buf)
    return buf.getvalue()
