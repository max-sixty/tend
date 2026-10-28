"""Tests for the credential-injection addon (GitHub + Anthropic).

Run: ``uv run pytest`` from the repo root, which covers every Python suite.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import inject_credentials
import pytest
import setup_sandbox
from graphql import build_schema, graphql_sync
from inject_credentials import (
    ANTHROPIC_HOSTS,
    BASIC_HOSTS,
    TOKEN_HOSTS,
    CredentialInjector,
    _creation,
)
from mitmproxy.test import tflow, tutils

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_proxy_starts_and_finishes_its_empty_replay(tmp_path: Path) -> None:
    empty_flows = tmp_path / "empty.flows"
    empty_flows.touch()
    confdir = tmp_path / "conf"
    mitmdump = Path(sys.executable).with_name("mitmdump")

    result = subprocess.run(
        [
            mitmdump,
            "-s",
            REPO_ROOT / "proxy" / "inject_credentials.py",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            "0",
            "--set",
            f"confdir={confdir}",
            "--allow-hosts",
            r"^example\.invalid$",
            "--set",
            f"rfile={empty_flows}",
        ],
        env=os.environ
        | {
            "TEND_GH_TOKEN": "dummy",
            "TEND_ANTHROPIC_OAUTH_TOKEN": "dummy",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "HTTP(S) proxy listening at 127.0.0.1:" in result.stdout
    assert (confdir / "mitmproxy-ca-cert.pem").is_file()


def _allow_hosts_regex() -> re.Pattern[str]:
    # mitmproxy compiles --allow-hosts with re.IGNORECASE; model that here.
    return re.compile(setup_sandbox.ALLOW_HOSTS, re.IGNORECASE)


def test_allow_hosts_regex_covers_every_injected_host() -> None:
    # The frozensets scope injection; the allow-hosts regex in setup_sandbox.py
    # scopes TLS interception. A host in a frozenset the regex misses is never
    # intercepted, so its dummy is never swapped for the real secret and every
    # call to it 401s — with the proxy alive, the CA trusted and both files
    # looking correct in isolation. Both sides carry a "keep in sync" comment;
    # only this asserts it. mitmproxy appends the port to every candidate it
    # matches (peer address, Host header, SNI), so `host:port` is the form
    # interception actually turns on — the bare host is extra strictness, not
    # what production feeds the regex.
    allow_hosts = _allow_hosts_regex()
    for host in BASIC_HOSTS | TOKEN_HOSTS | ANTHROPIC_HOSTS:
        assert allow_hosts.search(host), f"{host} is injected but never intercepted"
        assert allow_hosts.search(f"{host}:443"), f"{host}:443 is never intercepted"


@pytest.mark.parametrize(
    "host",
    [
        # Signed, time-limited URLs; injecting there breaks the download, so the
        # addon leaves it out of TOKEN_HOSTS and the regex must not widen
        # interception to it either.
        "objects.githubusercontent.com",
        # The lookalikes the injection tests below already refuse a credential
        # for. Interception is the outer boundary and should refuse them first.
        "api.github.com.evil.example",
        "raw.githubusercontent.com.evil.example",
        "api.anthropic.com.evil.example",
        "pypi.org",
    ],
)
def test_allow_hosts_regex_intercepts_nothing_extra(host: str) -> None:
    allow_hosts = _allow_hosts_regex()
    assert not allow_hosts.search(host), f"{host} should not be intercepted"
    assert not allow_hosts.search(f"{host}:443"), (
        f"{host}:443 should not be intercepted"
    )


def test_addon_methods_are_real_mitmproxy_hooks() -> None:
    # mitmproxy loads an addon whose method names don't match any event without
    # complaining, and simply never calls them — the proxy would come up and
    # inject nothing, failing every authenticated call. Nothing about the
    # process (alive, listening, CA written) shows that, so pin the names
    # against mitmproxy's own registry.
    import mitmproxy.proxy.layers.http  # noqa: F401  (registers the HTTP hooks)
    from mitmproxy import hooks

    known = {hook.name for hook in hooks.all_hooks.values()}
    declared = {name for name in vars(CredentialInjector) if not name.startswith("_")}
    assert declared, "the addon declares no hooks at all"
    assert declared <= known, f"not mitmproxy events: {sorted(declared - known)}"


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
    monkeypatch.delenv("TEND_GITHUB_ONLY", raising=False)
    return CredentialInjector()


@pytest.fixture
def api_key_injector(monkeypatch: pytest.MonkeyPatch) -> CredentialInjector:
    """GitHub token + Anthropic API key (the alternate auth mode)."""
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    monkeypatch.setenv("TEND_ANTHROPIC_API_KEY", "sk-ant-api03-REAL")
    monkeypatch.delenv("TEND_ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("TEND_GITHUB_ONLY", raising=False)
    return CredentialInjector()


# --- GitHub --------------------------------------------------------------


def test_api_host_gets_token_scheme(injector: CredentialInjector) -> None:
    flow = _flow("api.github.com", {"Authorization": "token ghp_dummy"})
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_REALTOKEN"


def test_git_host_gets_basic_scheme(injector: CredentialInjector) -> None:
    flow = _flow(
        "github.com",
        {"Authorization": "Basic " + base64.b64encode(b"x:dummy").decode()},
    )
    asyncio.run(injector.request(flow))
    expected = "Basic " + base64.b64encode(b"x-access-token:ghp_REALTOKEN").decode()
    assert flow.request.headers["Authorization"] == expected


def test_missing_authorization_is_added_for_github(
    injector: CredentialInjector,
) -> None:
    # git's first request is unauthenticated; the proxy authenticates it so git
    # never needs a credential of its own.
    flow = _flow("github.com")
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"].startswith("Basic ")


def test_raw_content_host_gets_token_scheme(injector: CredentialInjector) -> None:
    # Private raw.githubusercontent.com content authenticates a PAT via the
    # ``token`` scheme, same as the API hosts.
    flow = _flow("raw.githubusercontent.com", {"Authorization": "token ghp_dummy"})
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_REALTOKEN"


def test_raw_lookalike_host_is_untouched(injector: CredentialInjector) -> None:
    flow = _flow(
        "raw.githubusercontent.com.evil.example", {"Authorization": "token ghp_dummy"}
    )
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_object_store_host_is_untouched(injector: CredentialInjector) -> None:
    # objects.githubusercontent.com serves release assets / git-LFS objects from
    # signed URLs and batch-provided tokens; the PAT must NOT be injected or it
    # collides with the signature and breaks the download.
    flow = _flow("objects.githubusercontent.com", {"Authorization": "token ghp_dummy"})
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_non_github_host_is_untouched(injector: CredentialInjector) -> None:
    flow = _flow("pypi.org", {"Authorization": "token ghp_dummy"})
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_lookalike_host_is_untouched(injector: CredentialInjector) -> None:
    flow = _flow("api.github.com.evil.example", {"Authorization": "token ghp_dummy"})
    asyncio.run(injector.request(flow))
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
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_mixed_case_host_still_gets_credential(injector: CredentialInjector) -> None:
    # Clients send the hostname case-preserved and --allow-hosts matches
    # case-insensitively, so a mixed-case host is intercepted and must still
    # hit the (lowercase) injection allowlist.
    flow = _flow("Api.GitHub.Com")
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_REALTOKEN"


def test_plain_http_is_never_injected(injector: CredentialInjector) -> None:
    # A cleartext request to an allowlisted host must not carry the real
    # secret — it would transit unencrypted.
    flow = _flow("api.github.com", {"Authorization": "token ghp_dummy"}, scheme="http")
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


def test_uppercase_http_scheme_is_never_injected(
    injector: CredentialInjector,
) -> None:
    # The guard compares `scheme != "https"` case-sensitively, so it holds only
    # while mitmproxy normalizes the scheme it parses off the wire. mitmproxy
    # 12.2.2 started accepting absolute-form requests with an uppercase scheme
    # that earlier versions rejected outright, which routes a new shape here.
    flow = _flow("api.github.com", {"Authorization": "token ghp_dummy"}, scheme="HTTP")
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "token ghp_dummy"


# --- Anthropic -----------------------------------------------------------


def test_anthropic_host_gets_oauth_bearer(injector: CredentialInjector) -> None:
    flow = _flow("api.anthropic.com", {"Authorization": "Bearer sk-ant-oat01-dummy"})
    asyncio.run(injector.request(flow))
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
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-oat01-REAL"
    assert "x-api-key" not in flow.request.headers


def test_anthropic_host_gets_api_key(api_key_injector: CredentialInjector) -> None:
    flow = _flow("api.anthropic.com", {"x-api-key": "sk-ant-api03-dummy"})
    asyncio.run(api_key_injector.request(flow))
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
    asyncio.run(api_key_injector.request(flow))
    assert flow.request.headers["x-api-key"] == "sk-ant-api03-REAL"
    assert "Authorization" not in flow.request.headers


def test_anthropic_oauth_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    monkeypatch.setenv("TEND_ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-REAL")
    monkeypatch.setenv("TEND_ANTHROPIC_API_KEY", "sk-ant-api03-REAL")
    injector = CredentialInjector()
    flow = _flow("api.anthropic.com", {"x-api-key": "sk-ant-api03-dummy"})
    asyncio.run(injector.request(flow))
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-oat01-REAL"
    assert "x-api-key" not in flow.request.headers


def test_anthropic_lookalike_host_is_untouched(injector: CredentialInjector) -> None:
    flow = _flow("api.anthropic.com.evil.example", {"x-api-key": "dummy"})
    asyncio.run(injector.request(flow))
    assert flow.request.headers["x-api-key"] == "dummy"
    assert "Authorization" not in flow.request.headers


def test_anthropic_mixed_case_host_gets_credential(
    injector: CredentialInjector,
) -> None:
    flow = _flow("Api.Anthropic.Com", {"Authorization": "Bearer sk-ant-oat01-dummy"})
    asyncio.run(injector.request(flow))
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
    monkeypatch.delenv("TEND_GITHUB_ONLY", raising=False)
    with pytest.raises(RuntimeError, match="Anthropic credential"):
        CredentialInjector()


def test_github_only_mode_needs_no_anthropic_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEND_GH_TOKEN", "ghp_REALTOKEN")
    monkeypatch.setenv("TEND_GITHUB_ONLY", "1")
    monkeypatch.delenv("TEND_ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("TEND_ANTHROPIC_API_KEY", raising=False)
    github_only = CredentialInjector()
    flow = _flow("api.anthropic.com", {"x-api-key": "dummy"})
    asyncio.run(github_only.request(flow))
    assert flow.request.headers["x-api-key"] == "dummy"


def _api_flow(path: str, *, method: str = "POST", body: dict | None = None):
    flow = _flow("api.github.com")
    flow.request.method = method
    flow.request.path = path
    flow.request.text = json.dumps(body or {})
    return flow


@pytest.mark.parametrize(
    ("query", "variables", "operation", "expected"),
    [
        (
            "mutation($input:CreateIssueInput!){new:createIssue(input:$input){issue{id}}}",
            {"input": {"repositoryId": "R_1", "title": "a"}},
            None,
            {"repository_id": "R_1", "response_key": "new"},
        ),
        (
            (
                "mutation { ...Make } fragment Make on Mutation { "
                'createPullRequest(input:{repositoryId:"R_1", title:"a", baseRefName:"main", '
                'headRefName:"fix"}) { pullRequest { id } } }'
            ),
            {},
            None,
            {"repository_id": "R_1", "response_key": "createPullRequest"},
        ),
        (
            (
                "query Read { viewer { login } } mutation Make { "
                'createIssue(input:{repositoryId:"R_1", title:"a"}) {issue{id}}}'
            ),
            {},
            "Read",
            None,
        ),
        (
            (
                "mutation($make:Boolean=false) { ...Make @include(if:$make) } "
                'fragment Make on Mutation { createIssue(input:{repositoryId:"R_1", '
                'title:"a"}){issue{id}}}'
            ),
            {},
            None,
            None,
        ),
        (
            (
                "mutation($make:Boolean=false) { ... on Mutation @include(if:$make) { "
                'createIssue(input:{repositoryId:"R_1", title:"a"}){issue{id}}}}'
            ),
            {"make": True},
            None,
            {"repository_id": "R_1", "response_key": "createIssue"},
        ),
    ],
)
def test_creation_selection_follows_graphql_execution(
    query: str, variables: dict, operation: str | None, expected: dict | None
) -> None:
    flow = _api_flow(
        "/graphql",
        body={"query": query, "variables": variables, "operationName": operation},
    )
    assert _creation(flow.request) == expected


def test_exhaustion_blocks_only_creation(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = []

    async def exhausted(payload):
        reads.append(payload)
        return {"allowed": False, "in_scope": True, "message": "Close #42 to approve."}

    monkeypatch.setattr(injector, "_check_budget", exhausted)
    creates = [
        _api_flow("/repos/owner/repo/issues"),
        _api_flow("/repos/owner/repo/pulls"),
        _api_flow(
            "/graphql",
            body={
                "query": "mutation { createIssue(input: "
                '{repositoryId:"R_1", title:"a"}){issue{id}}}'
            },
        ),
    ]
    for flow in creates:
        asyncio.run(injector.request(flow))
        assert flow.response.status_code == 429
        assert json.loads(flow.response.text) == {"message": "Close #42 to approve."}
    assert [read.get("repository") or read.get("repository_id") for read in reads] == [
        "owner/repo",
        "owner/repo",
        "R_1",
    ]
    for flow in [
        _api_flow("/repos/owner/repo/issues/3/comments"),
        _api_flow(
            "/repos/owner/repo/issues/3", method="PATCH", body={"state": "closed"}
        ),
        _api_flow("/repos/owner/repo/pulls/3/reviews"),
        _api_flow(
            "/graphql",
            body={"query": 'mutation { closeIssue(input: {issueId:"I_1"}){issue{id}}}'},
        ),
        _flow("github.com"),
    ]:
        asyncio.run(injector.request(flow))
        assert flow.response is None
        assert "Authorization" in flow.request.headers
    assert len(reads) == 3


def test_creation_reservations_cover_inflight_requests_and_reconcile_receipts(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []

    async def scenario():
        checking, release = asyncio.Event(), asyncio.Event()

        async def budget(payload):
            seen.append(json.loads(json.dumps(payload)))
            checking.set()
            await release.wait()
            return {
                "allowed": not payload["pending"],
                "in_scope": True,
                "message": "Full",
            }

        monkeypatch.setattr(injector, "_check_budget", budget)
        first = _api_flow("/repos/owner/repo/issues")
        first_task = asyncio.create_task(injector.request(first))
        await checking.wait()
        comment = _api_flow("/repos/owner/repo/issues/3/comments")
        await injector.request(comment)
        assert not first_task.done()
        assert comment.response is None
        release.set()
        await first_task
        second = _api_flow("/repos/owner/repo/issues")
        await injector.request(second)
        assert second.response.status_code == 429
        assert seen[1]["pending"][0]["id"] is None
        first.response = tutils.tresp(status_code=201)
        first.response.text = json.dumps({"node_id": "I_1"})
        injector.responseheaders(first)
        assert first.response.stream is False
        injector.response(first)
        third = _api_flow("/repos/owner/repo/issues")
        await injector.request(third)
        assert seen[2]["pending"][0]["id"] == "I_1"

    asyncio.run(scenario())


def test_slow_admission_reserves_at_forwarding_time(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tend.runtime.shared.creation_limit import balance

    class Clock(datetime):
        current = datetime(2026, 10, 27, 23, 59, 59, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(inject_credentials, "datetime", Clock)

    async def budget(payload):
        # GitHub's read completes on the next day. The proxy must charge
        # the creation to that day, when it actually forwards the request.
        Clock.current = datetime(2026, 10, 28, 0, 0, 1, tzinfo=UTC)
        tokens = balance(
            [datetime.fromisoformat(row["created_at"]) for row in payload["pending"]],
            Clock.current,
            capacity=10,
            per_day=10,
        )
        return {"allowed": tokens >= 1, "in_scope": True, "message": "Full"}

    monkeypatch.setattr(injector, "_check_budget", budget)
    for _ in range(10):
        creation = _api_flow("/repos/owner/repo/issues")
        asyncio.run(injector.request(creation))
        assert creation.response is None
    overflow = _api_flow("/repos/owner/repo/issues")
    asyncio.run(injector.request(overflow))
    assert overflow.response.status_code == 429


def test_failed_creation_releases_its_reservation(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []

    async def budget(payload):
        seen.append(payload)
        return {"allowed": True, "in_scope": True, "message": ""}

    monkeypatch.setattr(injector, "_check_budget", budget)
    first = _api_flow("/repos/owner/repo/issues")
    asyncio.run(injector.request(first))
    first.response = tutils.tresp(status_code=422)
    injector.response(first)
    asyncio.run(injector.request(_api_flow("/repos/owner/repo/issues")))
    assert seen[1]["pending"] == []


def test_graphql_field_error_keeps_the_persisted_creation_reserved(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def budget(payload):
        return {"allowed": not payload["pending"], "in_scope": True, "message": "Full"}

    monkeypatch.setattr(injector, "_check_budget", budget)
    schema = build_schema("""
        type Query { unused: Boolean }
        input CreateIssueInput { repositoryId: ID!, title: String! }
        type Mutation { createIssue(input: CreateIssueInput!): CreateIssuePayload! }
        type CreateIssuePayload { issue: Issue! }
        type Issue { id: ID! }
    """)
    persisted = []

    def create(_info, input):
        persisted.append(input)
        return {"issue": {"id": None}}

    query = (
        'mutation { createIssue(input:{repositoryId:"R_current",title:"a"}){issue{id}}}'
    )
    flow = _api_flow("/graphql", body={"query": query})
    asyncio.run(injector.request(flow))
    result = graphql_sync(schema, query, root_value={"createIssue": create})
    assert len(persisted) == 1
    assert result.data is None
    assert result.errors[0].path == ["createIssue", "issue", "id"]
    flow.response = tutils.tresp(status_code=200)
    flow.response.text = json.dumps(result.formatted)
    injector.response(flow)

    next_creation = _api_flow("/repos/owner/repo/issues")
    asyncio.run(injector.request(next_creation))
    assert next_creation.response.status_code == 429


def test_missing_budget_evidence_does_not_stop_existing_work(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(_payload):
        raise OSError("GitHub unavailable")

    monkeypatch.setattr(injector, "_check_budget", unavailable)
    creation = _api_flow("/repos/owner/repo/issues")
    asyncio.run(injector.request(creation))
    assert creation.response.status_code == 503
    review = _api_flow("/repos/owner/repo/pulls/3/reviews")
    asyncio.run(injector.request(review))
    assert review.response is None


def test_budget_failure_surfaces_redacted_github_diagnostic(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailedProcess:
        returncode = 1

        async def communicate(self, _input):
            return b"", (
                b"discarded prefix "
                + b"x" * 2000
                + b"\nGitHub API 403: Resource not accessible (token ghp_REALTOKEN)"
            )

    async def launch(*_args, **_kwargs):
        return FailedProcess()

    monkeypatch.setenv("TEND_RUNTIME_PYTHON", sys.executable)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    creation = _api_flow("/repos/owner/repo/issues")
    asyncio.run(injector.request(creation))
    assert creation.response.status_code == 503
    message = json.loads(creation.response.text)["message"]
    assert "GitHub API 403: Resource not accessible" in message
    assert "ghp_REALTOKEN" not in message
    assert "[redacted]" in message
    assert "discarded prefix" not in message
    assert len(message) < 1600


def test_batched_creations_cannot_share_one_admission(
    injector: CredentialInjector,
) -> None:
    flow = _api_flow(
        "/graphql",
        body={
            "query": "mutation { "
            'a:createIssue(input:{repositoryId:"R_1",title:"a"}){issue{id}} '
            'b:createIssue(input:{repositoryId:"R_1",title:"b"}){issue{id}} }'
        },
    )
    asyncio.run(injector.request(flow))
    assert flow.response.status_code == 400
    assert "one creation" in flow.response.text


def test_foreign_creation_does_not_consume_local_budget(
    injector: CredentialInjector, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def foreign(_payload):
        return {"allowed": True, "in_scope": False, "message": ""}

    monkeypatch.setattr(injector, "_check_budget", foreign)
    flow = _api_flow("/repos/elsewhere/other/issues")
    asyncio.run(injector.request(flow))
    assert flow.response is None
    assert "tend_creation" not in flow.metadata


def test_proxy_calls_the_isolated_runtime_with_direct_github_access(
    injector: CredentialInjector,
    installed_runtime: tuple[Path, Path],
    actions_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only GitHub is replaced. The installed, isolated CLI reads its real
    # environment and sends its JSON decision through the production subprocess
    # boundary. An accidental proxy loop or dummy credential fails in fake gh.
    python, _ = installed_runtime
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "gh"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

assert os.environ["GH_TOKEN"] == os.environ["GITHUB_TOKEN"] == "ghp_REALTOKEN"
assert not any(key.lower() in {"http_proxy", "https_proxy", "all_proxy"} for key in os.environ)
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args == ["api", "user"]:
    result = {"login": "tend-agent", "id": 4242}
elif args == ["api", "repos/owner/repo"]:
    result = {"node_id": "R_current"}
elif args[:3] == ["api", "--paginate", "--slurp"] and "issues?" in args[3]:
    stamp = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    count = int(Path(os.environ["FAKE_GH_COUNT"]).read_text())
    result = [[{"node_id": f"I_{i}", "created_at": stamp} for i in range(count)]]
elif args[:3] == ["api", "--paginate", "--slurp"] and "/events?" in args[3]:
    result = [[]]
elif args[:2] == ["issue", "list"]:
    result = [{"number": 42, "title": "Bot rate limit reached"}]
elif args[:2] in [["issue", "reopen"], ["issue", "comment"]]:
    print("tracker updated")
    sys.exit(0)
else:
    sys.exit("unexpected gh call: " + repr(args))
print(json.dumps(result))
"""
    )
    fake.chmod(0o755)
    log, count = tmp_path / "calls.jsonl", tmp_path / "count"
    count.write_text("10")
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("FAKE_GH_COUNT", str(count))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TEND_RUNTIME_PYTHON", str(python))
    monkeypatch.setenv("GH_TOKEN", "agent-dummy")
    monkeypatch.setenv("GITHUB_TOKEN", "agent-dummy")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")

    blocked = _api_flow("/repos/owner/repo/issues")
    asyncio.run(injector.request(blocked))
    assert blocked.response.status_code == 429
    assert "#42" in json.loads(blocked.response.text)["message"]
    calls = log.read_text()
    assert '"reopen", "42"' in calls
    review = _api_flow("/repos/owner/repo/pulls/3/reviews")
    asyncio.run(injector.request(review))
    assert review.response is None
    assert log.read_text() == calls

    count.write_text("0")
    # gh's actual IssueCreate selection returns id and url; that ID reconciles
    # a successful local reservation once it appears in the repository listing.
    allowed = _api_flow(
        "/graphql",
        body={
            "query": (
                "mutation IssueCreate($input:CreateIssueInput!){"
                "createIssue(input:$input){issue{id url}}}"
            ),
            "variables": {"input": {"repositoryId": "R_current", "title": "a"}},
        },
    )
    asyncio.run(injector.request(allowed))
    assert allowed.response is None
    allowed.response = tutils.tresp(status_code=200)
    allowed.response.text = json.dumps(
        {
            "data": {
                "createIssue": {
                    "issue": {
                        "id": "I_created",
                        "url": "https://github.com/owner/repo/issues/100",
                    }
                }
            }
        }
    )
    injector.response(allowed)
    assert injector._receipts[allowed.id]["id"] == "I_created"
