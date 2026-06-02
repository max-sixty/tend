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
attached to those four GitHub hosts and nothing else — a request to any other
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

# git's smart-HTTP transport authenticates with Basic (token as the password);
# the REST and upload hosts take the ``token`` scheme. ``pretty_host`` carries
# no port, so these are bare hostnames.
GIT_HOSTS = frozenset({"github.com", "codeload.github.com"})
API_HOSTS = frozenset({"api.github.com", "uploads.github.com"})


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
        host = flow.request.pretty_host
        if host in API_HOSTS:
            flow.request.headers["Authorization"] = f"token {self._token}"
        elif host in GIT_HOSTS:
            flow.request.headers["Authorization"] = self._basic
        else:
            # Not a GitHub host — never attach the token. Leave the request
            # exactly as the agent sent it.
            return
        logging.info("injected GitHub credential for %s %s", flow.request.method, host)


addons = [GitHubAuthInjector()]
