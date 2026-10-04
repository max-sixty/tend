"""Synchronize agent-authored Markdown memory with one secret GitHub Gist.

The caller supplies the Gist ID; descriptions are not unique enough to be a
key. ``restore`` downloads the memory directory and records a baseline.
``save`` uploads the local change set only when every changed remote file still
matches that baseline when read. If any changed file conflicts, no files are
patched. Gist PATCH has no atomic precondition, so truly simultaneous writes
can still race and synchronization remains best-effort.

The CLI owns the shared restore/save/cleanup lifecycle for both harnesses.
Memory and its signing key live outside the disposable runtime container and
the runner home visible through the sandbox view. Only memory is made writable
by the agent; the per-run key stays runner-owned and never enters its environment.
Claude uses native auto memory; Codex receives Tend's read/write instructions.

This is opt-in and limited to public repositories. A secret Gist is unlisted,
not private, and the agent decides what is worth remembering. The Gist ID
still appears in the ``gh api /gists/<id>`` argv.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import _common
from _safe_files import read_regular_nofollow

BASELINE_FILE = ".tend-gist-baseline.json"
SETTINGS_FILE = ".tend-settings.json"
MAX_FILE_BYTES = 1_000_000
MAX_BASELINE_BYTES = 64 * 1024 * 1024
MEMORY_PARENT = Path("/var/tmp")
DIRECTORY_PREFIX = "tend-auto-memory."
KEY_PREFIX = "tend-auto-memory-key."

_GIST_ID = re.compile(r"^[A-Za-z0-9]+$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_BASELINE_KEY = re.compile(r"^[a-f0-9]{64}$")


class GistMemoryError(RuntimeError):
    """The remote or local memory cannot be synchronized safely."""


def _is_memory_filename(name: str) -> bool:
    """Whether *name* is one safe top-level Markdown filename."""
    return (
        bool(name)
        and Path(name).name == name
        and name not in {".", ".."}
        and name.endswith(".md")
        and not any(
            unicodedata.category(character).startswith("C") for character in name
        )
    )


def _api(path: str, *, method: str = "GET", payload: dict | None = None) -> Any:
    try:
        if method == "GET":
            return _common.gh_json("api", path)
        _common.gh(
            "api",
            path,
            "-X",
            method,
            "--input",
            "-",
            input=json.dumps(payload),
        )
        return None
    except subprocess.CalledProcessError as error:
        raise GistMemoryError("GitHub API request failed") from error
    except json.JSONDecodeError as error:
        raise GistMemoryError(
            f"GitHub API returned invalid JSON: {error.msg}"
        ) from error


def _validate_inputs(
    gist_id: str,
    repository: str,
    gist_owner: str,
    directory: Path,
    baseline_key: str,
) -> None:
    if not _GIST_ID.fullmatch(gist_id):
        raise GistMemoryError("Gist ID must be alphanumeric")
    if not _REPOSITORY.fullmatch(repository):
        raise GistMemoryError("repository must be owner/name")
    if not _GITHUB_LOGIN.fullmatch(gist_owner):
        raise GistMemoryError("Gist owner must be a GitHub login")
    if not directory.is_absolute() or directory == Path("/"):
        raise GistMemoryError("memory directory must be an absolute non-root path")
    if not _BASELINE_KEY.fullmatch(baseline_key):
        raise GistMemoryError("baseline key must be 32 bytes encoded as lowercase hex")


def _require_public_repository(repository: str) -> None:
    response = _api(f"/repos/{repository}")
    if not isinstance(response, dict) or response.get("visibility") != "public":
        raise GistMemoryError(
            "Gist-backed auto memory is available only for public repositories"
        )


def _gist_files(gist_id: str, repository: str, gist_owner: str) -> dict[str, str]:
    response = _api(f"/gists/{gist_id}")
    description = f"tend auto memory: {repository}"
    if (
        not isinstance(response, dict)
        or response.get("id") != gist_id
        or response.get("description") != description
        or response.get("public") is not False
        or response.get("truncated") is not False
        or not isinstance(response.get("owner"), dict)
        or not isinstance(response["owner"].get("login"), str)
        or response["owner"]["login"].casefold() != gist_owner.casefold()
        or not isinstance(response.get("files"), dict)
        or "MEMORY.md" not in response["files"]
    ):
        raise GistMemoryError(
            f"Gist must be owned by {gist_owner}, secret, complete, and "
            f"described as '{description}'"
        )

    files: dict[str, str] = {}
    for name, value in response["files"].items():
        if not isinstance(name, str) or not _is_memory_filename(name):
            raise GistMemoryError(f"unsupported memory filename: {name!r}")
        if (
            not isinstance(value, dict)
            or value.get("truncated") is not False
            or not isinstance(value.get("content"), str)
        ):
            raise GistMemoryError(f"memory file is truncated or malformed: {name}")
        content = value["content"]
        if len(content.encode()) > MAX_FILE_BYTES:
            raise GistMemoryError(f"memory file exceeds the 1 MB inline limit: {name}")
        files[name] = content
    return files


def _baseline_path(directory: Path) -> Path:
    return directory / BASELINE_FILE


def _settings_path(directory: Path) -> Path:
    return directory / SETTINGS_FILE


def _signed_baseline(
    repository: str, files: dict[str, str], baseline_key: str
) -> dict[str, object]:
    baseline: dict[str, object] = {"repository": repository, "files": files}
    canonical = json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
    baseline["signature"] = hmac.new(
        bytes.fromhex(baseline_key), canonical, hashlib.sha256
    ).hexdigest()
    return baseline


def restore(
    gist_id: str,
    repository: str,
    gist_owner: str,
    directory: Path,
    baseline_key: str,
) -> int:
    """Restore the Gist into *directory* and record its exact baseline."""
    _validate_inputs(gist_id, repository, gist_owner, directory, baseline_key)
    _require_public_repository(repository)
    files = _gist_files(gist_id, repository, gist_owner)

    if directory.is_symlink():
        raise GistMemoryError("memory directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise GistMemoryError("memory directory is not empty")
    for name, content in files.items():
        (directory / name).write_text(content)
    _baseline_path(directory).write_text(
        json.dumps(_signed_baseline(repository, files, baseline_key))
    )
    print(f"Gist memory: restored {len(files)} file(s)")
    return 0


def _read_baseline(
    repository: str, directory: Path, baseline_key: str
) -> dict[str, str]:
    try:
        body = read_regular_nofollow(
            _baseline_path(directory), max_bytes=MAX_BASELINE_BYTES
        )
        if body is None:
            raise FileNotFoundError(_baseline_path(directory))
        value = json.loads(body.decode())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise GistMemoryError("memory baseline is missing or malformed") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"repository", "files", "signature"}
        or value.get("repository") != repository
        or not isinstance(value.get("files"), dict)
        or not isinstance(value.get("signature"), str)
        or not all(
            isinstance(name, str) and isinstance(content, str)
            for name, content in value["files"].items()
        )
    ):
        raise GistMemoryError("memory baseline is malformed or belongs to another repo")
    expected = _signed_baseline(repository, value["files"], baseline_key)["signature"]
    if not hmac.compare_digest(value["signature"], expected):
        raise GistMemoryError("memory baseline was modified during the agent run")
    return value["files"]


def _local_files(directory: Path) -> dict[str, str]:
    if directory.is_symlink():
        raise GistMemoryError("memory directory must not be a symlink")
    files: dict[str, str] = {}
    for path in directory.iterdir():
        if path.is_symlink():
            raise GistMemoryError(f"memory entry must not be a symlink: {path.name!r}")
        if path.is_dir():
            raise GistMemoryError(
                f"nested memory directories are not supported by Gists: {path.name!r}"
            )
        if not path.is_file() or not path.name.endswith(".md"):
            continue
        if not _is_memory_filename(path.name):
            raise GistMemoryError(f"unsupported memory filename: {path.name!r}")
        try:
            body = read_regular_nofollow(path, max_bytes=MAX_FILE_BYTES)
            if body is None:
                continue
            content = body.decode()
        except (OSError, UnicodeError, ValueError) as error:
            raise GistMemoryError(
                f"memory file is invalid or exceeds the 1 MB inline limit: {path.name}"
            ) from error
        files[path.name] = content
    if "MEMORY.md" not in files:
        raise GistMemoryError("local memory has no MEMORY.md entrypoint")
    return files


def save(
    gist_id: str,
    repository: str,
    gist_owner: str,
    directory: Path,
    baseline_key: str,
) -> int:
    """Save local changes only when the entire change set is conflict-free."""
    _validate_inputs(gist_id, repository, gist_owner, directory, baseline_key)
    _require_public_repository(repository)
    if directory.is_symlink():
        raise GistMemoryError("memory directory must not be a symlink")
    baseline = _read_baseline(repository, directory, baseline_key)
    local = _local_files(directory)
    remote = _gist_files(gist_id, repository, gist_owner)

    writes: dict[str, dict[str, str] | None] = {}
    conflicts: list[str] = []
    for name in sorted(set(baseline) | set(local)):
        before = baseline.get(name)
        after = local.get(name)
        if after == before:
            continue
        current = remote.get(name)
        if current not in (before, after):
            conflicts.append(name)
            continue
        if current == after:
            continue
        writes[name] = None if after is None else {"content": after}

    if conflicts:
        print(
            "Gist memory: skipped entire save because remote changes conflict in "
            + ", ".join(conflicts),
            file=sys.stderr,
        )
        print("Gist memory: saved 0 file(s)")
        return 0
    if writes:
        _api(f"/gists/{gist_id}", method="PATCH", payload={"files": writes})
    print(f"Gist memory: saved {len(writes)} file(s)")
    return 0


def _working_paths() -> tuple[Path, Path]:
    """Restrict privileged cleanup to this lifecycle's two mktemp shapes."""
    values = _common.require_env(
        "TEND_AUTO_MEMORY_DIRECTORY", "TEND_AUTO_MEMORY_KEY_FILE"
    )
    paths = (
        Path(values["TEND_AUTO_MEMORY_DIRECTORY"]),
        Path(values["TEND_AUTO_MEMORY_KEY_FILE"]),
    )
    for path, prefix in zip(paths, (DIRECTORY_PREFIX, KEY_PREFIX)):
        if path.parent != MEMORY_PARENT or not re.fullmatch(
            re.escape(prefix) + r"[A-Za-z0-9_]+", path.name
        ):
            raise GistMemoryError("invalid memory working path")
    return paths


def _remove_working_copy(directory: Path, key_file: Path) -> None:
    subprocess.run(
        ["/usr/bin/sudo", "/usr/bin/rm", "-rf", "--", str(directory), str(key_file)],
        check=True,
    )


def prepare(gist_id: str, repository: str, gist_owner: str) -> int:
    """Restore before launch, keeping the signing key outside agent access."""
    values = _common.require_env("SANDBOX", "TEND_HARNESS", "GITHUB_ENV")
    harness = values["TEND_HARNESS"]
    if harness not in {"claude", "codex"}:
        raise GistMemoryError(f"unknown memory harness: {harness}")
    directory = Path(tempfile.mkdtemp(prefix=DIRECTORY_PREFIX, dir=MEMORY_PARENT))
    key_file: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=KEY_PREFIX, dir=MEMORY_PARENT)
        key_file = Path(name)
        baseline_key = secrets.token_hex(32)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(baseline_key)
        restore(gist_id, repository, gist_owner, directory, baseline_key)
        exports = {
            "TEND_AUTO_MEMORY_DIRECTORY": str(directory),
            "TEND_AUTO_MEMORY_KEY_FILE": str(key_file),
        }
        if harness == "claude":
            settings = _settings_path(directory)
            settings.write_text(
                json.dumps(
                    {
                        "autoMemoryDirectory": str(directory),
                        "autoMemoryEnabled": True,
                    }
                )
            )
            exports["TEND_AUTO_MEMORY_SETTINGS"] = str(settings)
        subprocess.run(
            [
                "/usr/bin/sudo",
                "/usr/bin/chown",
                "-R",
                f"{values['SANDBOX']}:{values['SANDBOX']}",
                str(directory),
            ],
            check=True,
        )
        with Path(values["GITHUB_ENV"]).open("a") as stream:
            for name, value in exports.items():
                stream.write(f"{name}={value}\n")
    except BaseException:
        # Even a failed ownership handoff can leave sandbox-owned files.
        if key_file is not None:
            _remove_working_copy(directory, key_file)
        else:
            directory.rmdir()
        raise
    return 0


def finish(gist_id: str, repository: str, gist_owner: str) -> int:
    """Save after the supervisor has reaped every sandbox process."""
    directory, key_file = _working_paths()
    if directory.is_symlink():
        raise GistMemoryError("memory directory must not be a symlink")
    subprocess.run(
        [
            "/usr/bin/sudo",
            "/usr/bin/chown",
            "--recursive",
            "--no-dereference",
            f"{os.getuid()}:{os.getgid()}",
            str(directory),
        ],
        check=True,
    )
    baseline_key = key_file.read_text()
    return save(gist_id, repository, gist_owner, directory, baseline_key)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in {"restore", "save", "cleanup"}:
        print(
            "usage: gist_memory.py restore|save|cleanup (configuration is read from the environment)",
            file=sys.stderr,
        )
        return 2
    command = args[0]
    try:
        if command == "cleanup":
            _remove_working_copy(*_working_paths())
            return 0
        values = _common.require_env(
            "GITHUB_TOKEN",
            "GITHUB_REPOSITORY",
            "TEND_MEMORY_GIST_ID",
            "TEND_AUTO_MEMORY_GIST_OWNER",
        )
        operation = prepare if command == "restore" else finish
        return operation(
            values["TEND_MEMORY_GIST_ID"],
            values["GITHUB_REPOSITORY"],
            values["TEND_AUTO_MEMORY_GIST_OWNER"],
        )
    except (
        GistMemoryError,
        OSError,
        UnicodeError,
        subprocess.CalledProcessError,
    ) as error:
        print(
            f"::warning::Experimental memory {command} failed: {error}", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
