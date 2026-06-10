"""Tests for the GitHub credential-injection addon.

Run: ``uv run --with mitmproxy --with pytest python -m pytest interactive/proxy``
"""

from __future__ import annotations

import base64

import pytest
from mitmproxy.test import tflow, tutils

from github_auth import GitHubAuthInjector


def _flow(host: str, authorization: str | None = None) -> object:
    flow = tflow.tflow(req=tutils.treq(host=host))
    if authorization is not None:
        flow.request.headers["Authorization"] = authorization
    return flow


@pytest.fixture
def injector(monkeypatch: pytest.MonkeyPatch) -> GitHubAuthInjector:
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    return GitHubAuthInjector()


def test_api_host_gets_token_scheme(injector: GitHubAuthInjector) -> None:
    flow = _flow("api.github.com", "token ghp_dummy")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_REALTOKEN"


def test_git_host_gets_basic_scheme(injector: GitHubAuthInjector) -> None:
    flow = _flow("github.com", "Basic " + base64.b64encode(b"x:dummy").decode())
    injector.request(flow)
    expected = "Basic " + base64.b64encode(b"x-access-token:ghp_REALTOKEN").decode()
    assert flow.request.headers["Authorization"] == expected


def test_raw_content_host_gets_token_scheme(injector: GitHubAuthInjector) -> None:
    # Private raw.githubusercontent.com content authenticates a PAT via the
    # ``token`` scheme, same as the API hosts.
    flow = _flow("raw.githubusercontent.com", "token ghp_dummy")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_REALTOKEN"


def test_raw_lookalike_host_is_untouched(injector: GitHubAuthInjector) -> None:
    flow = _flow("raw.githubusercontent.com.evil.example", "token ghp_dummy")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_object_store_host_is_untouched(injector: GitHubAuthInjector) -> None:
    # objects.githubusercontent.com serves release assets / git-LFS objects from
    # signed URLs and batch-provided tokens; the PAT must NOT be injected or it
    # collides with the signature and breaks the download.
    flow = _flow("objects.githubusercontent.com", "token ghp_dummy")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_missing_authorization_is_added_for_github(injector: GitHubAuthInjector) -> None:
    # git's first request is unauthenticated; the proxy authenticates it so git
    # never needs a credential of its own.
    flow = _flow("github.com")
    injector.request(flow)
    assert flow.request.headers["Authorization"].startswith("Basic ")


def test_non_github_host_is_untouched(injector: GitHubAuthInjector) -> None:
    flow = _flow("pypi.org", "token ghp_dummy")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_lookalike_host_is_untouched(injector: GitHubAuthInjector) -> None:
    flow = _flow("api.github.com.evil.example", "token ghp_dummy")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_spoofed_host_header_does_not_leak_token(injector: GitHubAuthInjector) -> None:
    # The real connection target is an attacker host, but the client spoofs the
    # Host header to a GitHub host. The token must NOT be injected — otherwise it
    # would be forwarded to the attacker. Guards against gating on pretty_host.
    flow = _flow("attacker.example", "token ghp_dummy")
    flow.request.headers["Host"] = "api.github.com"
    assert flow.request.pretty_host == "api.github.com"  # the spoofable view
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_no_token_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEND_GH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TEND_GH_TOKEN is unset"):
        GitHubAuthInjector()
