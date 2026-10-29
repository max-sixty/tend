"""Smoke the pinned uv installer and frozen proxy dependency group on Linux.

An empty replay reaches mitmdump's running hook before its KeepServing addon
exits. Port zero gives this probe its own listener; successful startup must
produce both the listener log and CA certificate used by the real sandbox.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    temporary = Path(os.environ["RUNNER_TEMP"])
    uv_dir = temporary / "tend-uv"
    subprocess.run(
        ["bash", str(root / "shared/steps/install-uv.sh")],
        env={**os.environ, "UV_INSTALL_DIR": str(uv_dir)},
        check=True,
    )
    confdir = temporary / "tend-mitm-conf"
    log = temporary / "tend-mitm.log"
    flows = temporary / "tend-empty.flows"
    flows.touch()
    environment = {
        **os.environ,
        "UV_PROJECT_ENVIRONMENT": str(temporary / "tend-proxy-venv"),
    }
    command = [
        str(uv_dir / "uv"),
        "run",
        "--no-config",
        "--no-python-downloads",
        "--python",
        "/usr/bin/python3",
        "--project",
        str(root),
        "--frozen",
        "--no-build",
        "--only-group",
        "proxy",
        "mitmdump",
    ]
    subprocess.run(
        [*command, "--version"], env=environment, stdout=subprocess.DEVNULL, check=True
    )
    with log.open("w") as output:
        result = subprocess.run(
            [
                *command,
                "-s",
                "inject_credentials.py",
                "--listen-host",
                "127.0.0.1",
                "--listen-port",
                "0",
                "--set",
                f"confdir={confdir}",
                "--set",
                f"rfile={flows}",
                "--allow-hosts",
                r"^example\.invalid$",
            ],
            cwd=root / "proxy",
            env={
                **environment,
                "TEND_GH_TOKEN": "dummy",
                "TEND_ANTHROPIC_OAUTH_TOKEN": "dummy",
            },
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    output = log.read_text()
    if result.returncode:
        print(output, end="")
        raise SystemExit(
            "::error::mitmdump failed to start from the locked proxy group"
        )
    if "HTTP(S) proxy listening at 127.0.0.1:" not in output:
        raise SystemExit("mitmdump did not report its listener")
    if not (confdir / "mitmproxy-ca-cert.pem").is_file():
        raise SystemExit("mitmdump did not create its CA certificate")


if __name__ == "__main__":
    main()
