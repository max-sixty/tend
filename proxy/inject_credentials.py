"""mitmproxy addon: inject the bot's real credentials into the requests that
need them, so the sandboxed agent never holds the secrets.

Two credentials, one mechanism. This process runs as the privileged runner
user, OUTSIDE the agent's sandbox. The real secrets are read once at startup
and live only in this process's memory. The agent runs as a separate, non-sudo
UID and therefore cannot read this process's environment
(``/proc/<pid>/environ`` is owner-only) or escalate to it. The agent holds only
throwaway dummies; every request it makes to an allowlisted host has its
credential header replaced here with the real one.

- **GitHub** (``TEND_GH_TOKEN``): the git smart-HTTP hosts authenticate with
  Basic (token as the password); the REST, upload, and raw-content hosts take
  the ``token`` scheme.
- **Anthropic** (``TEND_ANTHROPIC_OAUTH_TOKEN`` or ``TEND_ANTHROPIC_API_KEY``):
  ``api.anthropic.com`` only. The agent's ``claude`` binary runs in the SAME
  auth mode with a dummy secret, so it already emits every mode-specific header
  (``anthropic-beta``, ``anthropic-version``, ``x-api-key`` vs
  ``Authorization``); this addon only swaps the dummy secret value for the real
  one. OAuth wins when both are set, matching the action's input precedence.

The Codex harness sets ``TEND_GITHUB_ONLY=1`` because OpenAI's purpose-built
Responses API proxy owns model authentication there.

New issues and pull requests pass through the installed runtime's creation
budget before GitHub receives them. Other requests keep working when the
budget is exhausted. Local reservations cover concurrent creates and GitHub
listing lag within this process; independent runners still share a best-effort
budget rather than an atomic reservation service.

Security boundary: the injection allowlist below is exact-match on
``flow.request.host`` — the real connection target, NOT ``pretty_host``, which
mitmproxy derives from the spoofable client-supplied Host header. A request to
any other host (including ``api.github.com.evil.com`` or a host intercepted by
misconfiguration) is passed through untouched. The proxy's ``--allow-hosts``
flag is a functionality optimization (don't TLS-intercept non-allowlisted
traffic, so package managers keep working); this allowlist is the credential
boundary.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit

from graphql import GraphQLError, get_operation_ast, parse, value_from_ast_untyped
from graphql.language import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    OperationType,
)
from mitmproxy import http

# GitHub hosts grouped by the header injected, not by role. git's smart-HTTP
# transport authenticates with Basic (token as the password); the REST, upload,
# and raw-content hosts take the ``token`` scheme. Bare hostnames
# (``flow.request.host`` carries no port).
#
# raw.githubusercontent.com serves private raw file content and authenticates a
# PAT via ``Authorization: token``, so it joins the token group. Deliberately
# NOT covered: objects.githubusercontent.com (release assets, git-LFS objects),
# which download from signed, time-limited URLs — git-LFS gets a short-lived
# object credential from the batch API on github.com (already covered) — so
# injecting the PAT there would collide with the signature and break the
# download; that host stays an untouched tunnel.
#
# These frozensets are the credential boundary. The proxy's --allow-hosts regex
# in setup_sandbox.py scopes TLS interception and must list the same hosts —
# keep the two in sync (a host here but not in the regex is never intercepted,
# so its dummy is never swapped and auth 401s). The
# `test_allow_hosts_regex_*` tests in test_inject_credentials.py read that
# regex out of the script and fail on drift in either direction.
BASIC_HOSTS = frozenset({"github.com", "codeload.github.com"})
TOKEN_HOSTS = frozenset(
    {"api.github.com", "uploads.github.com", "raw.githubusercontent.com"}
)
ANTHROPIC_HOSTS = frozenset({"api.anthropic.com"})


def _creation(request: http.Request) -> dict[str, str] | None:
    """Select the creation the request actually executes, independent of aliases.

    GitHub's REST and GraphQL APIs are two routes to the same creation budget.
    Parsing GraphQL also keeps queries containing mutation-like strings, unused
    operations and skipped fields out of admission. Multiple creations in one
    mutation are refused: each creation needs its own admission result.
    """
    if request.method != "POST":
        return None
    path = unquote(urlsplit(request.path).path).rstrip("/")
    match = re.fullmatch(r"/repos/([^/]+/[^/]+)/(issues|pulls)", path)
    if match:
        return {"repository": match[1]}
    if path != "/graphql":
        return None
    payload = json.loads(request.get_text())
    document = parse(payload["query"])
    operation = get_operation_ast(document, payload.get("operationName"))
    if operation is None:
        raise ValueError("Specify one GraphQL operation.")
    if operation.operation != OperationType.MUTATION:
        return None
    variables = {
        definition.variable.name.value: value_from_ast_untyped(definition.default_value)
        for definition in operation.variable_definitions or ()
        if definition.default_value is not None
    } | (payload.get("variables") or {})
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }

    def fields(selection_set, visited=frozenset()):
        for selection in selection_set.selections:
            skip = False
            for directive in selection.directives or ():
                if directive.name.value not in {"skip", "include"}:
                    continue
                condition = next(
                    argument.value
                    for argument in directive.arguments
                    if argument.name.value == "if"
                )
                value = value_from_ast_untyped(condition, variables)
                if value is not True and value is not False:
                    raise ValueError("GraphQL include/skip requires a boolean.")
                skip |= value if directive.name.value == "skip" else not value
            if skip:
                continue
            if isinstance(selection, FieldNode):
                yield selection
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                if name in visited:
                    raise ValueError("GraphQL fragment cycle.")
                yield from fields(fragments[name].selection_set, visited | {name})
            else:
                yield from fields(selection.selection_set, visited)

    # GraphQL merges repeated selections with the same response key, including
    # a field reached through more than one fragment.
    creations = {
        (field.alias or field.name).value: field
        for field in fields(operation.selection_set)
        if field.name.value in {"createIssue", "createPullRequest"}
    }
    if not creations:
        return None
    if len(creations) != 1:
        raise ValueError("Create one issue or pull request per GraphQL operation.")
    response_key, field = next(iter(creations.items()))
    input_node = next(arg.value for arg in field.arguments if arg.name.value == "input")
    repository_id = value_from_ast_untyped(input_node, variables)["repositoryId"]
    if not isinstance(repository_id, str) or not repository_id:
        raise ValueError("Creation requires a repositoryId.")
    return {"repository_id": repository_id, "response_key": response_key}


def _refuse(flow: http.HTTPFlow, status: int, message: str) -> None:
    flow.response = http.Response.make(
        status, json.dumps({"message": message}), {"Content-Type": "application/json"}
    )


class CredentialInjector:
    def __init__(self) -> None:
        gh_token = os.environ.get("TEND_GH_TOKEN", "")
        if not gh_token:
            raise RuntimeError(
                "TEND_GH_TOKEN is unset — refusing to start the credential "
                "proxy with no GitHub token to inject"
            )
        self._gh_token = gh_token
        self._creation_lock = asyncio.Lock()
        self._receipts: dict[str, dict[str, str | None]] = {}
        # Basic base64("x-access-token:<token>") — GitHub's git endpoint accepts
        # the token as the password with this fixed username.
        self._gh_basic = (
            "Basic " + base64.b64encode(f"x-access-token:{gh_token}".encode()).decode()
        )

        # Exactly one Anthropic scheme is active, matching the action's
        # precedence (OAuth wins when both are present).
        self._anthropic_oauth = os.environ.get("TEND_ANTHROPIC_OAUTH_TOKEN", "")
        self._anthropic_api_key = os.environ.get("TEND_ANTHROPIC_API_KEY", "")
        github_only = os.environ.get("TEND_GITHUB_ONLY") == "1"
        if not github_only and not (self._anthropic_oauth or self._anthropic_api_key):
            raise RuntimeError(
                "Neither TEND_ANTHROPIC_OAUTH_TOKEN nor TEND_ANTHROPIC_API_KEY "
                "is set — refusing to start the credential proxy with no "
                "Anthropic credential to inject"
            )

    async def request(self, flow: http.HTTPFlow) -> None:
        # Never attach a credential to a cleartext request — a plain
        # http:// URL to an allowlisted host would otherwise carry the real
        # secret unencrypted (GitHub only redirects to https AFTER the header
        # has transited).
        if flow.request.scheme != "https":
            return
        # Gate on `host` — the real connection target — NOT `pretty_host`,
        # which mitmproxy derives from the client-supplied Host header. A
        # sandboxed agent could otherwise send `Host: api.github.com` to an
        # attacker host and have the real token injected and forwarded there.
        # Lowercase it: clients send the hostname case-preserved on the wire,
        # and mitmproxy's --allow-hosts matches case-insensitively, so a
        # mixed-case `Api.GitHub.Com` is intercepted and must still match.
        host = flow.request.host.lower()
        if host == "api.github.com":
            try:
                creation = _creation(flow.request)
            except (ValueError, KeyError, TypeError, StopIteration, GraphQLError):
                _refuse(
                    flow,
                    400,
                    "Invalid creation request; use one creation per operation.",
                )
                return
            if creation is not None and not await self._admit(flow, creation):
                return
        headers = flow.request.headers
        if host in TOKEN_HOSTS:
            headers["Authorization"] = f"token {self._gh_token}"
        elif host in BASIC_HOSTS:
            headers["Authorization"] = self._gh_basic
        elif host in ANTHROPIC_HOSTS and (
            self._anthropic_oauth or self._anthropic_api_key
        ):
            # Normalize to exactly the active scheme so the injected credential
            # is the one Anthropic honors, even if the agent crafted both.
            if self._anthropic_oauth:
                headers["Authorization"] = f"Bearer {self._anthropic_oauth}"
                if "x-api-key" in headers:
                    del headers["x-api-key"]
            else:
                headers["x-api-key"] = self._anthropic_api_key
                if "Authorization" in headers:
                    del headers["Authorization"]
        else:
            # Not an allowlisted host — never attach a credential. Leave the
            # request exactly as the agent sent it.
            return
        # mitmproxy's documented addon pattern logs through the root logger, which
        # its own handler captures; a named logger would change where this lands.
        logging.info(  # noqa: LOG015
            "injected credential for %s %s", flow.request.method, host
        )

    async def _admit(self, flow: http.HTTPFlow, creation: dict[str, str]) -> bool:
        # Only creation admission serializes; reviews, git transfers and model
        # streams continue while GitHub supplies the budget evidence. Reserve
        # before forwarding so concurrent creates in this proxy see each other.
        async with self._creation_lock:
            now = datetime.now(UTC)
            floor = (now - timedelta(days=1)).isoformat()
            self._receipts = {
                key: receipt
                for key, receipt in self._receipts.items()
                if receipt["created_at"] >= floor
            }
            try:
                result = await self._check_budget(
                    {
                        key: value
                        for key, value in creation.items()
                        if key != "response_key"
                    }
                    | {"pending": list(self._receipts.values())}
                )
                if not result["allowed"]:
                    _refuse(flow, 429, result["message"])
                    return False
                if result["in_scope"]:
                    self._receipts[flow.id] = {
                        "id": None,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                    flow.metadata["tend_creation"] = creation
            except (OSError, ValueError, KeyError, TypeError, TimeoutError) as error:
                detail = str(error)
                if self._gh_token:
                    detail = detail.replace(self._gh_token, "[redacted]")
                _refuse(
                    flow,
                    503,
                    "Creation budget could not be read; retry this creation. "
                    + detail[-1500:],
                )
                return False
        return True

    async def _check_budget(self, payload: dict) -> dict:
        # The installed runtime owns budget arithmetic and approval records. It
        # runs outside the sandbox, directly against GitHub with the real token.
        # Do not inherit proxy routing: that would loop this request back here.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
        } | {"GITHUB_TOKEN": self._gh_token, "GH_TOKEN": self._gh_token}
        process = await asyncio.create_subprocess_exec(
            os.environ["TEND_RUNTIME_PYTHON"],
            "-I",
            "-m",
            "tend",
            "runtime",
            "creation-limit",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode()), timeout=120
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ValueError(
                "Creation budget command failed: " + stderr.decode(errors="replace")
            )
        result = json.loads(stdout)
        if (
            type(result["allowed"]) is not bool
            or type(result["in_scope"]) is not bool
            or not isinstance(result["message"], str)
        ):
            raise ValueError("Invalid creation budget response.")
        return result

    def response(self, flow: http.HTTPFlow) -> None:
        creation = flow.metadata.get("tend_creation")
        if creation is None:
            return
        if 400 <= flow.response.status_code < 500:
            self._receipts.pop(flow.id, None)
            return
        if not 200 <= flow.response.status_code < 300:
            # A transport/server failure may follow an accepted creation. Keep
            # that reservation rather than manufacture available capacity.
            return
        try:
            payload = json.loads(flow.response.get_text())
            if "response_key" in creation:
                item = (payload.get("data") or {}).get(creation["response_key"])
                # A caller may omit receipt fields in its selection. Retain an
                # anonymous reservation when the response cannot identify it.
                # GraphQL errors can also null the payload after creation has
                # persisted, so an HTTP 200 error is an unknown outcome.
                item = (
                    (item or {}).get("issue") or (item or {}).get("pullRequest") or {}
                )
                identifier, stamp = item.get("id"), item.get("createdAt")
            else:
                identifier, stamp = payload.get("node_id"), payload.get("created_at")
            receipt = self._receipts[flow.id]
            if isinstance(identifier, str):
                receipt["id"] = identifier
            if isinstance(stamp, str):
                receipt["created_at"] = datetime.fromisoformat(stamp).isoformat()
        except (ValueError, KeyError, TypeError):
            # The request reached GitHub but its outcome is not readable. The
            # reservation remains conservative until its evidence window ends.
            return

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        # Stream ordinary responses straight through for every allowlisted
        # host, not just Anthropic. Without this, mitmproxy
        # buffers the entire body in memory before relaying a single byte, which
        # breaks two cases: Anthropic's SSE inference responses arrive as one
        # blob only after the full generation (long turns hit the client's
        # first-byte timeout, mitmproxy#4469), and a large git packfile from a
        # GitHub clone/fetch is held whole in proxy memory before relaying.
        # Do NOT reach for `--set stream_large_bodies` instead: that
        # also streams large REQUEST bodies, whose headers are forwarded upstream
        # before the request hook fires — silently disabling credential injection
        # on exactly the large-context calls that need it.
        # Creation receipts are small and let admission reconcile local
        # reservations with GitHub's durable history on the next creation.
        flow.response.stream = "tend_creation" not in flow.metadata


addons = [CredentialInjector()]
