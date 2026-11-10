"""Setup provides one isolated Tend command without replacing consumer Python."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("sandbox_exit", [0, 42])
def test_installer_exposes_isolated_tend_only(
    installed_runtime: tuple[Path, Path], tmp_path: Path, sandbox_exit: int
) -> None:
    python, _ = installed_runtime
    # Replace only uv's already-tested installation. The production shell
    # installs the launcher and publishes its actual PATH entry.
    uv_dir = tmp_path / "uv"
    uv_dir.mkdir()
    uv = uv_dir / "uv"
    installations = tmp_path / "installations"
    uv.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$UV_PROJECT_ENVIRONMENT" >> {shlex.quote(str(installations))}\n'
        f'case "$UV_PROJECT_ENVIRONMENT" in */venv) test {sandbox_exit} -eq 0 || exit {sandbox_exit} ;; esac\n'
        f'ln -s {shlex.quote(str(python.parent.parent))} "$UV_PROJECT_ENVIRONMENT"\n'
    )
    uv.chmod(0o755)
    temporary = tmp_path / "runner with spaces"
    temporary.mkdir()
    github_env = tmp_path / "github-env"
    github_path = tmp_path / "github-path"
    runtime_root = tmp_path / "sandbox"
    runtime_root.mkdir()
    install = ["/bin/bash", str(ROOT / "shared/steps/install-runtime.sh")]
    setup_env = {
        **os.environ,
        "ACTION_PATH": str(ROOT),
        "UV_INSTALL_DIR": str(uv_dir),
        "RUNNER_TEMP": str(temporary),
        "GITHUB_ENV": str(github_env),
        "GITHUB_PATH": str(github_path),
        "TEND_RUNTIME_ROOT": str(runtime_root),
        "TEND_PRIVATE_DIR": "",
        "TEND_RUNTIME_PYTHON": "",
    }
    missing = subprocess.run(
        [*install, "sandbox"],
        env=setup_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "Run max-sixty/tend/setup-runtime" in missing.stderr
    assert not installations.exists()
    subprocess.run(
        [*install, "runner"], env=setup_env, check=True, capture_output=True, text=True
    )
    assert not (runtime_root / "venv").exists()
    consumer = tmp_path / "consumer-bin"
    consumer.mkdir()
    for name, body in {"python": "echo consumer-python", "tend": "exit 98"}.items():
        path = consumer / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    consumer_path = f"{consumer}:{github_path.read_text().strip()}:/usr/bin:/bin"
    sandbox = subprocess.run(
        [*install, "sandbox"],
        env={
            **setup_env,
            "PATH": consumer_path,
            "TEND_RUNTIME_PYTHON": str(temporary / "tend-python/bin/python"),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert sandbox.returncode == sandbox_exit, sandbox.stderr
    assert installations.read_text().splitlines() == [
        str(temporary / "tend-python"),
        str(runtime_root / "venv"),
    ]
    additions = github_path.read_text().splitlines()
    assert additions == [str(temporary / "tend-python/commands")] * 2
    commands = Path(additions[-1])
    assert [path.name for path in commands.iterdir()] == ["tend"]
    assert (
        github_env.read_text()
        == f"TEND_RUNTIME_PYTHON={temporary}/tend-python/bin/python\n"
    )
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    package = hostile / "tend"
    package.mkdir()
    (package / "__init__.py").write_text(
        'raise RuntimeError("consumer package imported")\n'
    )
    (hostile / "sitecustomize.py").write_text(
        'raise RuntimeError("consumer site imported")\n'
    )
    startup = hostile / "startup.sh"
    startup.write_text('echo "consumer shell startup ran" >&2; exit 99\n')
    env = {
        **os.environ,
        "PATH": f"{commands}:{consumer_path}",
        "PYTHONPATH": str(hostile),
        "PYTHONHOME": str(hostile),
        "PYTHONUSERBASE": str(hostile),
        "BASH_ENV": str(startup),
        "ENV": str(startup),
        "GITHUB_REPOSITORY": "owner/repo",
    }
    result = subprocess.run(
        ["tend", "runtime", "triage-batch", "dispatch", "[]"],
        cwd=hostile,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    result = subprocess.run(
        ["python"], env=env, capture_output=True, text=True, check=True
    )
    assert result.stdout == "consumer-python\n"
