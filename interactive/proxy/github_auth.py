"""mitmproxy addon: inject the bot's GitHub token into requests to GitHub.

This process runs as the privileged runner user, OUTSIDE the agent's sandbox.
The real token is read once from ``TEND_GH_TOKEN`` at startup and lives only in
this process's memory. The sandboxed agent runs as a separate, non-sudo UID and
therefore cannot read this process's environment (``/proc/<pid>/environ`` is
owner-only) or escalate to it. The agent holds only a throwaway dummy token;
every request it makes to a GitHub host has its ``Authorization`` header
replaced here with the real token, so ``gh`` and ``git`` authenticate without
the credential ever entering the sandbox.

Security boundary: the injection allowlist below is exact-match. The token is
attached to those five GitHub hosts and nothing else — a request to any other
host (including ``api.github.com.evil.com`` or a host mitmproxy intercepts by
misconfiguration) is passed through untouched. The proxy's ``--allow-hosts``
flag is a functionality optimization (don't TLS-intercept non-GitHub traffic so
package managers keep working); this allowlist is the credential boundary.

Scope: GitHub only. The Claude/LLM credential is out of scope for this cut and
still reaches the agent through its own environment.
"""

from __future__ import annotations

import base64
import logging
import os

from mitmproxy import http

# Grouped by the header we inject, not by role. git's smart-HTTP transport
# authenticates with Basic (token as the password); the REST, upload, and
# raw-content hosts take the ``token`` scheme. These are bare hostnames
# (``flow.request.host`` carries no port).
#
# raw.githubusercontent.com serves private raw file content and authenticates a
# PAT via ``Authorization: token``, so it joins the token group. Deliberately
# NOT covered: objects.githubusercontent.com (release assets, git-LFS objects).
# Those download from signed, time-limited URLs, and git-LFS gets a short-lived
# object credential from the batch API on github.com (already covered here).
# Injecting the PAT there would collide with the signature / batch token and
# break the download, so that host stays an untouched tunnel.
BASIC_HOSTS = frozenset({"github.com", "codeload.github.com"})
TOKEN_HOSTS = frozenset(
    {"api.github.com", "uploads.github.com", "raw.githubusercontent.com"}
)


class GitHubAuthInjector:
    def __init__(self) -> None:
        token = os.environ.get("TEND_GH_TOKEN", "")
        if not token:
            raise RuntimeError(
                "TEND_GH_TOKEN is unset — refusing to start the credential "
                "proxy with no token to inject"
            )
        self._token = token
        # Basic base64("x-access-token:<token>") — GitHub's git endpoint accepts
        # the token as the password with this fixed username.
        self._basic = "Basic " + base64.b64encode(
            f"x-access-token:{token}".encode()
        ).decode()

    def request(self, flow: http.HTTPFlow) -> None:
        # Gate on `host` — the real connection target — NOT `pretty_host`, which
        # mitmproxy derives from the client-supplied Host header. A sandboxed
        # agent could otherwise send `Host: api.github.com` to an attacker host
        # and have the real token injected and forwarded there.
        host = flow.request.host
        if host in TOKEN_HOSTS:
            flow.request.headers["Authorization"] = f"token {self._token}"
        elif host in BASIC_HOSTS:
            flow.request.headers["Authorization"] = self._basic
        else:
            # Not a GitHub host — never attach the token. Leave the request
            # exactly as the agent sent it.
            return
        logging.info("injected GitHub credential for %s %s", flow.request.method, host)


addons = [GitHubAuthInjector()]
