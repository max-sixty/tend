"""mitmproxy imports the addon module and reads its module-level ``addons``
list, so the addon is constructed at import — which requires the credential env
vars. In production the proxy is always launched with them set; seed dummies
here so importing the module for tests behaves the same. Individual tests
override them via ``monkeypatch``.
"""

import os

os.environ.setdefault("TEND_GH_TOKEN", "ghp_conftest_dummy")
os.environ.setdefault("TEND_ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-conftest-dummy")
