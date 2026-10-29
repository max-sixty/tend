"""Setup provides one isolated Tend command without replacing consumer Python."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_installer_exposes_isolated_tend_only(
    installed_runtime: tuple[Path, Path], tmp_path: Path
) -> None:
    python, _ = installed_runtime
    # Replace only uv's already-tested installation. The production shell
    # installs the launcher and publishes its actual PATH entry.
    uv_dir = tmp_path / "uv"
    uv_dir.mkdir()
    uv = uv_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'ln -s {shlex.quote(str(python.parent.parent))} "$UV_PROJECT_ENVIRONMENT"\n'
    )
    uv.chmod(0o755)
    temporary = tmp_path / "runner with spaces"
    temporary.mkdir()
    github_env = tmp_path / "github-env"
    github_path = tmp_path / "github-path"
    subprocess.run(
        ["/bin/bash", str(ROOT / "shared/steps/install-runtime.sh")],
        env={
            **os.environ,
            "ACTION_PATH": str(ROOT),
            "UV_INSTALL_DIR": str(uv_dir),
            "RUNNER_TEMP": str(temporary),
            "GITHUB_ENV": str(github_env),
            "GITHUB_PATH": str(github_path),
            "TEND_RUNTIME_ROOT": "",
            "TEND_PRIVATE_DIR": "",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    commands = Path(github_path.read_text().strip())
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
    consumer = tmp_path / "consumer-bin"
    consumer.mkdir()
    for name, body in {"python": "echo consumer-python", "tend": "exit 98"}.items():
        path = consumer / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{commands}:{consumer}:/usr/bin:/bin",
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
