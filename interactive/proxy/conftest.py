"""mitmproxy imports the addon module and reads its module-level ``addons``
list, so the addon is constructed at import — which requires ``TEND_GH_TOKEN``.
In production the proxy is always launched with it set; seed a dummy here so
importing the module for tests behaves the same. Individual tests override it
via ``monkeypatch``.
"""

import os

os.environ.setdefault("TEND_GH_TOKEN", "ghp_conftest_dummy")
