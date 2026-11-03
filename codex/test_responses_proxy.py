"""Exercise the proxy lifecycle with an external stand-in for OpenAI's CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tend.runtime.codex import responses_proxy

from _fakes import GithubFiles


@pytest.fixture
def proxy_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Replace the external Node/proxy and privileged chown, not our lifecycle."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TEND_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_PROXY_BIN", str(tmp_path / "proxy.js"))
    monkeypatch.setenv("PROXY_API_KEY", "test-proxy-key")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-must-not-cross")
    monkeypatch.setenv("NODE_OPTIONS", "--inspect=0.0.0.0:9229")
    (bindir / "sudo").write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:3] == ['chown', 'root:root']\n"
        "Path(sys.argv[3]).with_suffix('.sealed').write_text('root:root')\n"
    )
    (bindir / "sudo").chmod(0o755)
    processes = []
    popen = subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(responses_proxy.subprocess, "Popen", tracked_popen)

    def install(mode="ready"):
        (bindir / "node").write_text(
            f"#!{sys.executable}\nMODE = {mode!r}\n"
            + """
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

info = Path(sys.argv[-1])
key = sys.stdin.read()
info.with_suffix('.capture').write_text(json.dumps({
    'argv': sys.argv, 'env': dict(os.environ), 'stdin': key,
}))
if MODE == 'exit':
    print('proxy startup failed', file=sys.stderr, flush=True)
    sys.exit(9)
if MODE == 'timeout':
    time.sleep(60)
    sys.exit(1)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        info.with_suffix('.stopped').write_text(self.path)
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass

with HTTPServer(('127.0.0.1', 0), Handler) as server:
    payload = json.dumps({'port': server.server_port}) if MODE == 'ready' else MODE
    info.write_text(payload)
    server.handle_request()
"""
        )
        (bindir / "node").chmod(0o755)
        return tmp_path / "tend-codex-proxy.json", processes

    yield install
    for process in processes:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def test_start_and_stop_keep_credentials_outside_child_environment(
    proxy_process,
    github_files: GithubFiles,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    info, processes = proxy_process()
    assert responses_proxy.start() == 0
    capture = json.loads(info.with_suffix(".capture").read_text())
    assert capture["stdin"] == "test-proxy-key\n"
    assert capture["argv"][2:4] == ["--http-shutdown", "--server-info"]
    assert capture["env"]["NODE_OPTIONS"] == "--disable-sigusr1"
    assert "PROXY_API_KEY" not in capture["env"]
    assert "GITHUB_TOKEN" not in capture["env"]
    assert "test-proxy-key" not in json.dumps(capture["argv"])
    assert info.stat().st_mode & 0o777 == 0o444
    assert info.with_suffix(".sealed").read_text() == "root:root"
    exported = github_files.env.read_text()
    port = json.loads(info.read_text())["port"]
    assert exported == f"CODEX_PROXY_URL=http://127.0.0.1:{port}\n"
    assert "test-proxy-key" not in capsys.readouterr().out
    assert "test-proxy-key" not in info.with_suffix(".log").read_text()
    monkeypatch.setenv("CODEX_PROXY_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    assert responses_proxy.stop() == 0
    assert processes[0].wait(timeout=10) == 0
    assert info.with_suffix(".stopped").read_text() == "/shutdown"


@pytest.mark.parametrize(
    "mode", ["exit", "null", '{"port":true}', '{"port":65536}', "invalid-json"]
)
def test_startup_failure_does_not_export_a_url_or_leave_a_process(
    mode: str,
    proxy_process,
    github_files: GithubFiles,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, processes = proxy_process(mode)
    assert responses_proxy.start() == 1
    assert processes[0].poll() is not None
    assert github_files.env.read_text() == ""
    output = capsys.readouterr()
    assert "OpenAI Responses proxy did not start" in output.out
    assert "test-proxy-key" not in output.out + output.err


def test_startup_timeout_reaps_the_unready_process(
    proxy_process, github_files: GithubFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, processes = proxy_process("timeout")
    monkeypatch.setattr(responses_proxy, "STARTUP_TIMEOUT", 0)
    assert responses_proxy.start() == 1
    assert processes[0].poll() is not None
    assert github_files.env.read_text() == ""


def test_shutdown_is_optional_and_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_PROXY_URL", raising=False)
    assert responses_proxy.stop() == 0
    monkeypatch.setenv("CODEX_PROXY_URL", "http://127.0.0.1:0")
    assert responses_proxy.stop() == 0
