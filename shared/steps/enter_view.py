"""Give the agent a copy-on-write view of the runner's home, then drop into it.

Runs as root inside ``unshare --mount --propagation private`` and ends in
``exec``, so the mounts die with the process tree and need no cleanup.

The view is an overlay mounted over the home itself. Its lower layer is a
read-only bind of that home, **idmapped** so the runner's uid/gid and the
sandbox's swap places; its upper layer sits in the runtime container. The agent
reads what ``setup:`` left at the paths it left it, and its writes never reach
the runner's disk. bwrap resolves binds through the parent mount namespace, so
SRT's ``allowWrite`` re-bind of the home picks up the overlay unchanged.

The idmap is what makes the view writable: overlayfs checks the accessing
task's credentials against the lower inode's owner, so a ``runner:runner``
lower refuses every create by the sandbox uid. It is a swap with identity
elsewhere because overlayfs gives a copied-up inode the mapped lower owner, and
an unmapped one (a root-owned file ``setup:`` left) refuses the copy-up. Ids at
or above :data:`ID_CEILING` stay unmapped.

Swapping the two accounts and leaving every other id alone is what bounds the
view: the agent writes wherever the runner could, and no further. A directory
only root can write stays unwritable, and a root-owned file only root can read
stays unreadable — mapping uid 0 as well would hand the agent both, which is
more than the account whose home this is has.

``--mask`` paths inside the home are covered — a directory by an empty tmpfs, a
file by ``/dev/null`` — and ``agent_lifecycle.probe_view`` checks from inside
that the view is writable and the masks took. ``/tmp`` gets an empty tmpfs of
its own, so nothing SRT binds from under it is the host's.

Floors: kernel 5.19 and util-linux 2.39 (``X-mount.idmap``).
"""

from __future__ import annotations

import argparse
import os
import pwd
import subprocess
import sys
from pathlib import Path

from _safe_files import read_regular_nofollow

ID_CEILING = 65536
MOUNT = "/usr/bin/mount"
#: Well past what one execve accepts, so only a malformed file reaches it.
MAX_ENVIRONMENT = 4 * 1024 * 1024


def run(*argv: str) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        raise OSError(f"{' '.join(argv[:3])} failed: {result.stderr.strip()}")


def identity_map(kind: str, low: int, high: int) -> list[str]:
    """Swap ``low`` and ``high``, identity elsewhere, as ``X-mount.idmap`` entries.

    Each entry is ``<kind>:<id in the mount>:<id on disk>:<count>``.
    """
    if not 0 <= low <= high < ID_CEILING:
        raise ValueError(f"{kind} ids {low} and {high} are outside the mappable range")
    if low == high:
        return [f"{kind}:0:0:{ID_CEILING}"]
    entries = [f"{kind}:{low}:{high}:1", f"{kind}:{high}:{low}:1"]
    for start, count in (
        (0, low),
        (low + 1, high - low - 1),
        (high + 1, ID_CEILING - high - 1),
    ):
        if count:
            entries.append(f"{kind}:{start}:{start}:{count}")
    return entries


def swap_map(runner: pwd.struct_passwd, sandbox: pwd.struct_passwd) -> str:
    uids = identity_map("u", *sorted((runner.pw_uid, sandbox.pw_uid)))
    gids = identity_map("g", *sorted((runner.pw_gid, sandbox.pw_gid)))
    return " ".join(uids + gids)


def build_view(home: Path, stage: Path, sandbox: pwd.struct_passwd) -> None:
    home_stat = home.stat()
    runner = pwd.getpwuid(home_stat.st_uid)
    lower, upper, work = stage / "lower", stage / "upper", stage / "work"
    stage.mkdir(mode=0o700)
    for directory in (lower, upper, work):
        directory.mkdir()
    # The merged root takes the upper directory's owner and mode, so it has to
    # match the home as the idmap presents it.
    os.chown(upper, sandbox.pw_uid, sandbox.pw_gid)
    os.chmod(upper, home_stat.st_mode & 0o7777)
    idmap = swap_map(runner, sandbox)
    run(MOUNT, "--bind", "-o", f"ro,X-mount.idmap={idmap}", str(home), str(lower))
    # redirect_dir: renaming a directory that lives on the lower layer (cargo
    # does, in its registry) is EXDEV without it.
    options = f"lowerdir={lower},upperdir={upper},workdir={work},redirect_dir=on"
    run(MOUNT, "-t", "overlay", "overlay", "-o", options, str(home))
    # The overlay holds its own reference, and the bind still shows what the
    # masks below cover.
    run("/usr/bin/umount", "-l", str(lower))


def read_environment(path: Path) -> dict[bytes, bytes]:
    """The agent's environment, from the file the supervisor wrote, which it removes.

    NUL-separated ``NAME=VALUE`` entries; a later one wins, as with ``env``.
    """
    raw = read_regular_nofollow(path, max_bytes=MAX_ENVIRONMENT)
    if raw is None:
        raise ValueError(f"the launch environment {path} is missing")
    path.unlink()
    return dict(entry.split(b"=", 1) for entry in raw.split(b"\0") if entry)


def mask(path: Path) -> None:
    if path.is_dir():
        run(MOUNT, "-t", "tmpfs", "-o", "ro,mode=0555", "tmpfs", str(path))
    else:
        run(MOUNT, "--bind", "-o", "ro", "/dev/null", str(path))


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--user", required=True)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--mask", action="append", default=[], type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    # First, so no failure below leaves the file behind for the run's length.
    environment = read_environment(options.env_file)

    sandbox = pwd.getpwnam(options.user)
    build_view(options.home, options.stage, sandbox)
    for path in options.mask:
        mask(path)
    # SRT re-binds its default write path `/tmp/claude` whenever it exists
    # here, and bwrap binds what this namespace has, so a host `/tmp/claude`
    # would come back writable under SRT's own private /tmp and outlive the
    # run. Nothing SRT runs needs the host's /tmp: its sockets follow TMPDIR.
    run(MOUNT, "-t", "tmpfs", "-o", "mode=1777", "tmpfs", "/tmp")
    # A cwd inherited from the runner still names the host's inode.
    os.chdir(options.home)
    command = options.command[1:] if options.command[:1] == ["--"] else options.command
    argv = [
        "/usr/bin/setpriv",
        f"--reuid={sandbox.pw_uid}",
        f"--regid={sandbox.pw_gid}",
        "--clear-groups",
        # Nothing in the lifecycle execs a setuid binary. bwrap is unaffected:
        # a new user namespace resets the bounding set inside it.
        "--bounding-set=-all",
        *command,
    ]
    os.execve(argv[0], argv, environment)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (KeyError, OSError, ValueError) as problem:
        print(f"::error::view: {problem}", flush=True)
        raise SystemExit(1) from None
