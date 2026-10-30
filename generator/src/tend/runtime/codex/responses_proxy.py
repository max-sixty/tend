"""Start and stop Codex's runner-owned Responses API credential proxy.

The proxy receives its API key only through stdin and inherits only PATH and
the Node inspector restriction. Its server-info file is the readiness signal;
the validated loopback URL is exported for the sandbox and later teardown.
Subscription sessions never start this proxy. Teardown is best-effort so it
cannot replace the agent's result with a shutdown failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from tend.runtime.shared import _common

STARTUP_TIMEOUT = 10


def _ready_port(path: Path, process: subprocess.Popen, deadline: float) -> int:
    while True:
        if path.exists() and path.stat().st_size:
            info = json.loads(path.read_text())
            port = info.get("port") if isinstance(info, dict) else None
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("server-info must contain a valid TCP port")
            return port
        if process.poll() is not None:
            raise RuntimeError(f"proxy exited with status {process.returncode}")
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for server-info")
        time.sleep(0.1)


def start() -> int:
    env = _common.require_env("PROXY_API_KEY", "CODEX_PROXY_BIN", "TEND_PRIVATE_DIR")
    key = os.environ.pop("PROXY_API_KEY")
    private = Path(env["TEND_PRIVATE_DIR"])
    info = private / "tend-codex-proxy.json"
    log = private / "tend-codex-proxy.log"
    process = None
    try:
        with log.open("wb") as output:
            process = subprocess.Popen(
                [
                    "node",
                    env["CODEX_PROXY_BIN"],
                    "--http-shutdown",
                    "--server-info",
                    str(info),
                ],
                env={"PATH": os.environ["PATH"], "NODE_OPTIONS": "--disable-sigusr1"},
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        assert process.stdin is not None
        with process.stdin:
            process.stdin.write((key + "\n").encode())
        port = _ready_port(info, process, time.monotonic() + STARTUP_TIMEOUT)
        info.chmod(0o444)
        subprocess.run(["sudo", "chown", "root:root", str(info)], check=True)
        _common.set_env("CODEX_PROXY_URL", f"http://127.0.0.1:{port}")
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.CalledProcessError,
    ) as error:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if log.exists():
            sys.stderr.write(log.read_text(errors="replace").replace(key, "[redacted]"))
        return _common.fail(f"OpenAI Responses proxy did not start: {error}")
    return 0


def stop() -> int:
    url = os.environ.get("CODEX_PROXY_URL")
    if url:
        try:
            # This is a runner-local service, even if consumer setup exported
            # HTTP proxy settings for its own commands.
            with build_opener(ProxyHandler({})).open(f"{url}/shutdown", timeout=10):
                pass
        except (OSError, URLError, ValueError):
            _common.annotate("warning", "Could not stop OpenAI Responses proxy")
    return 0
