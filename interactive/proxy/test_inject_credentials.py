"""Tests for the credential-injection addon (GitHub + Anthropic).

Run: ``uv run --with mitmproxy --with pytest python -m pytest interactive/proxy``
"""

from __future__ import annotations

import base64

import pytest
from mitmproxy.test import tflow, tutils

from inject_credentials import CredentialInjector


def _flow(
    host: str, headers: dict[str, str] | None = None, scheme: str = "https"
) -> object:
    flow = tflow.tflow(req=tutils.treq(host=host))
    flow.request.scheme = scheme
    for name, value in (headers or {}).items():
        flow.request.headers[name] = value
    return flow


@pytest.fixture
def injector(monkeypatch: pytest.MonkeyPatch) -> CredentialInjector:
    """GitHub token + Anthropic OAuth (the default production shape)."""
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    monkeypatch.setenv("TEND_ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-REAL")
    monkeypatch.delenv("TEND_ANTHROPIC_API_KEY", raising=False)
    return CredentialInjector()


@pytest.fixture
def api_key_injector(monkeypatch: pytest.MonkeyPatch) -> CredentialInjector:
    """GitHub token + Anthropic API key (the alternate auth mode)."""
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    monkeypatch.setenv("TEND_ANTHROPIC_API_KEY", "sk-ant-api03-REAL")
    monkeypatch.delenv("TEND_ANTHROPIC_OAUTH_TOKEN", raising=False)
    return CredentialInjector()


# --- GitHub --------------------------------------------------------------


def test_api_host_gets_token_scheme(injector: CredentialInjector) -> None:
    flow = _flow("api.github.com", {"Authorization": "token ghp_dummy"})
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_REALTOKEN"


def test_git_host_gets_basic_scheme(injector: CredentialInjector) -> None:
    flow = _flow(
        "github.com",
        {"Authorization": "Basic " + base64.b64encode(b"x:dummy").decode()},
    )
    injector.request(flow)
    expected = "Basic " + base64.b64encode(b"x-access-token:ghp_REALTOKEN").decode()
    assert flow.request.headers["Authorization"] == expected


def test_missing_authorization_is_added_for_github(
    injector: CredentialInjector,
) -> None:
    # git's first request is unauthenticated; the proxy authenticates it so git
    # never needs a credential of its own.
    flow = _flow("github.com")
    injector.request(flow)
    assert flow.request.headers["Authorization"].startswith("Basic ")


def test_non_github_host_is_untouched(injector: CredentialInjector) -> None:
    flow = _flow("pypi.org", {"Authorization": "token ghp_dummy"})
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_lookalike_host_is_untouched(injector: CredentialInjector) -> None:
    flow = _flow("api.github.com.evil.example", {"Authorization": "token ghp_dummy"})
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_spoofed_host_header_does_not_leak_token(injector: CredentialInjector) -> None:
    # The real connection target is an attacker host, but the client spoofs the
    # Host header to a GitHub host. The token must NOT be injected — otherwise it
    # would be forwarded to the attacker. Guards against gating on pretty_host.
    flow = _flow(
        "attacker.example",
        {"Authorization": "token ghp_dummy", "Host": "api.github.com"},
    )
    assert flow.request.pretty_host == "api.github.com"  # the spoofable view
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_mixed_case_host_still_gets_credential(injector: CredentialInjector) -> None:
    # Clients send the hostname case-preserved and --allow-hosts matches
    # case-insensitively, so a mixed-case host is intercepted and must still
    # hit the (lowercase) injection allowlist.
    flow = _flow("Api.GitHub.Com")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_REALTOKEN"


def test_plain_http_is_never_injected(injector: CredentialInjector) -> None:
    # A cleartext request to an allowlisted host must not carry the real
    # secret — it would transit unencrypted.
    flow = _flow("api.github.com", {"Authorization": "token ghp_dummy"}, scheme="http")
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


# --- Anthropic -----------------------------------------------------------


def test_anthropic_host_gets_oauth_bearer(injector: CredentialInjector) -> None:
    flow = _flow("api.anthropic.com", {"Authorization": "Bearer sk-ant-oat01-dummy"})
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-oat01-REAL"


def test_anthropic_oauth_removes_stray_api_key(injector: CredentialInjector) -> None:
    # If the agent crafted both headers, only the active (OAuth) scheme survives.
    flow = _flow(
        "api.anthropic.com",
        {
            "Authorization": "Bearer sk-ant-oat01-dummy",
            "x-api-key": "sk-ant-api03-dummy",
        },
    )
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-oat01-REAL"
    assert "x-api-key" not in flow.request.headers


def test_anthropic_host_gets_api_key(api_key_injector: CredentialInjector) -> None:
    flow = _flow("api.anthropic.com", {"x-api-key": "sk-ant-api03-dummy"})
    api_key_injector.request(flow)
    assert flow.request.headers["x-api-key"] == "sk-ant-api03-REAL"


def test_anthropic_api_key_removes_stray_authorization(
    api_key_injector: CredentialInjector,
) -> None:
    flow = _flow(
        "api.anthropic.com",
        {
            "x-api-key": "sk-ant-api03-dummy",
            "Authorization": "Bearer sk-ant-oat01-dummy",
        },
    )
    api_key_injector.request(flow)
    assert flow.request.headers["x-api-key"] == "sk-ant-api03-REAL"
    assert "Authorization" not in flow.request.headers


def test_anthropic_oauth_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    monkeypatch.setenv("TEND_ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-REAL")
    monkeypatch.setenv("TEND_ANTHROPIC_API_KEY", "sk-ant-api03-REAL")
    injector = CredentialInjector()
    flow = _flow("api.anthropic.com", {"x-api-key": "sk-ant-api03-dummy"})
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-oat01-REAL"
    assert "x-api-key" not in flow.request.headers


def test_anthropic_lookalike_host_is_untouched(injector: CredentialInjector) -> None:
    flow = _flow("api.anthropic.com.evil.example", {"x-api-key": "dummy"})
    injector.request(flow)
    assert flow.request.headers["x-api-key"] == "dummy"
    assert "Authorization" not in flow.request.headers


def test_anthropic_mixed_case_host_gets_credential(injector: CredentialInjector) -> None:
    flow = _flow("Api.Anthropic.Com", {"Authorization": "Bearer sk-ant-oat01-dummy"})
    injector.request(flow)
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-oat01-REAL"


def test_responses_are_streamed(injector: CredentialInjector) -> None:
    # SSE inference responses must not be buffered (mitmproxy#4469) — the
    # addon streams every intercepted response through unmodified.
    flow = _flow("api.anthropic.com")
    flow.response = tutils.tresp()
    injector.responseheaders(flow)
    assert flow.response.stream is True


# --- Startup guards ------------------------------------------------------


def test_no_github_token_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEND_GH_TOKEN", raising=False)
    monkeypatch.setenv("TEND_ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-REAL")
    with pytest.raises(RuntimeError, match="TEND_GH_TOKEN is unset"):
        CredentialInjector()


def test_no_anthropic_credential_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    monkeypatch.delenv("TEND_ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("TEND_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Anthropic credential"):
        CredentialInjector()
