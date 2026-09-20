"""Give the agent a copy-on-write view of the runner's home, then drop into it.

This runs as root inside ``unshare --mount --propagation private``, started by
:mod:`launch_sandbox_runtime`, and ends in ``exec`` — so the whole boundary is
one process tree and the mounts below die with it. There is no cleanup step,
and nothing here can fail open: a mount that does not come up fails the run
before any process runs as the sandbox uid.

What it builds, at ``$HOME`` of the runner account:

1. A read-only **idmapped** bind of the runner's home, staged out of the way.
   The idmap swaps the runner's uid/gid with the sandbox's and is the identity
   everywhere else, so the tree presents as sandbox-owned.
2. An **overlay** whose lower layer is that bind, mounted over the home itself.
   The agent reads exactly what the job's ``setup:`` steps left, at the paths
   they left it, and every write it makes lands in an upper directory that no
   trusted process ever reads. The host filesystem is byte-for-byte unchanged.
3. Two read-only, mode-0 **tmpfs masks** over the parts of that home the agent
   must not read: the runner's own install directory (its service credentials)
   and GitHub's file-command directory (whatever earlier steps wrote to
   ``$GITHUB_ENV``/``$GITHUB_OUTPUT``). Both are derived from the running job
   rather than listed, so neither grows into a catalogue.

Why the overlay is mounted here rather than asked of the Sandbox Runtime: bwrap
resolves every bind source through its copy of the parent mount namespace, so a
mount made before launch is what it binds. SRT's own filesystem config is
same-path binds only, and its ``allowWrite`` re-bind of the home picks up this
overlay without SRT knowing it exists.

**The idmap is what makes the view writable.** Overlayfs checks the accessing
task's own credentials against the overlay inode, which mirrors the lower
inode's owner and mode; the mounter's credentials feed only a second check. A
``runner:runner`` lower is therefore EACCES for every create by the sandbox uid,
whoever mounted it. The swap repairs that, and it is a swap rather than a single
mapping because overlayfs takes a copied-up inode's owner from the mapped lower
stat: an unmapped owner is ``INVALID_UID``, which ``notify_change`` refuses, so
a root-owned directory ``setup:`` left behind would refuse every create beneath
it. Ids at or above :data:`ID_CEILING` are outside the map and present as the
kernel's overflow id; that is the documented ceiling of this design.

Floors: kernel 5.19 (idmapped layers under overlayfs) and util-linux 2.39
(``X-mount.idmap``). Both are met by the hosted ubuntu-24.04 image. A runner
that misses either fails the run naming the floor; there is no fallback path.
"""

from __future__ import annotations

import argparse
import os
import pwd
import subprocess
import sys
from pathlib import Path

#: Ids below this are mapped one-to-one (apart from the swap); ids at or above
#: it are not mapped at all and present as the kernel's overflow id. 64Ki
#: covers every account a runner image creates, the containers a ``setup:``
#: step starts, and root.
ID_CEILING = 65536

MOUNT = "/usr/bin/mount"
UMOUNT = "/usr/bin/umount"
FINDMNT = "/usr/bin/findmnt"


def fail(message: str) -> None:
    print(f"::error::view: {message}", flush=True)
    raise SystemExit(1)


def run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        fail(f"{' '.join(argv[:2])} failed: {detail}")
    return result.stdout


def identity_map(kind: str, low: int, high: int) -> list[str]:
    """Spell one id swap plus identity either side of it, for ``X-mount.idmap``.

    Each entry is ``<kind>:<id in the mount>:<id on disk>:<count>``, so the two
    one-id entries read "where the disk says `high`, the mount says `low`" and
    the reverse.
    """
    if not 0 <= low < high < ID_CEILING:
        raise ValueError(f"{kind} ids {low} and {high} are outside the mappable range")
    entries = [f"{kind}:{low}:{high}:1", f"{kind}:{high}:{low}:1"]
    for start, count in ((0, low), (low + 1, high - low - 1), (high + 1, ID_CEILING - high - 1)):
        if count:
            entries.append(f"{kind}:{start}:{start}:{count}")
    return entries


def swap_map(runner: pwd.struct_passwd, sandbox: pwd.struct_passwd) -> str:
    uids = identity_map("u", *sorted((runner.pw_uid, sandbox.pw_uid)))
    gids = identity_map("g", *sorted((runner.pw_gid, sandbox.pw_gid)))
    return " ".join(uids + gids)


def mount_facts(path: Path) -> tuple[str, str]:
    """Return the filesystem type and propagation of the mount holding ``path``."""
    fields = run([FINDMNT, "-n", "-o", "FSTYPE,PROPAGATION", "--target", str(path)])
    parts = fields.split()
    if len(parts) != 2:
        fail(f"findmnt gave no mount for {path}")
    return parts[0], parts[1]


def build_view(*, home: Path, stage: Path, sandbox: pwd.struct_passwd) -> None:
    owner = home.stat().st_uid
    try:
        runner = pwd.getpwuid(owner)
    except KeyError:
        fail(f"{home} is owned by uid {owner}, which is not an account")
    if runner.pw_uid == sandbox.pw_uid:
        fail(f"{home} is already the sandbox account's home")
    lower, upper, work = stage / "lower", stage / "upper", stage / "work"
    for directory in (stage, lower, upper, work):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    # The overlay option string is comma- and colon-separated, so a stage path
    # carrying either would be read as further options rather than as a path.
    # It is tend's own mktemp directory, never a consumer's, so this is an
    # assertion and not a sanitizer.
    if set(",:") & set(str(stage)):
        fail(f"view stage path must not contain ',' or ':': {stage}")

    run([MOUNT, "--bind", "-o", f"ro,X-mount.idmap={swap_map(runner, sandbox)}", str(home), str(lower)])
    run(
        [
            MOUNT,
            "-t",
            "overlay",
            "overlay",
            "-o",
            # redirect_dir, because renaming a directory that still lives on the
            # lower layer is EXDEV without it and cargo renames inside its
            # registry. Ubuntu's module defaults it off and only a real-root
            # mounter may turn it on.
            f"lowerdir={lower},upperdir={upper},workdir={work},redirect_dir=on",
            str(home),
        ]
    )
    # The overlay holds its own reference to the lower layer, so detaching the
    # path it was mounted at leaves nothing for a later process to find.
    run([UMOUNT, "-l", str(lower)])
    if os.path.ismount(lower):
        fail(f"the staged lower layer is still mounted at {lower}")


def mask(path: Path, *, home: Path) -> None:
    """Cover one directory inside the view with an empty, unreadable tmpfs."""
    if path != home and not path.is_relative_to(home):
        fail(f"{path} is not inside {home}, so masking it is not this view's business")
    if not path.is_dir():
        fail(f"{path} is not a directory, so the view cannot mask it")
    run([MOUNT, "-t", "tmpfs", "-o", "ro,nosuid,nodev,mode=0", "tmpfs", str(path)])


def verify(*, home: Path, masks: list[Path]) -> None:
    """Refuse to run the agent unless every mount this view needs is in place.

    Checked after the mounts rather than trusted from their exit status, and
    before the drop to the sandbox uid, so the one failure mode that would be
    silent — the agent running against the runner's real home — cannot happen.
    The propagation check is the other half of that: a view that propagated back
    to the host namespace would put the agent's writes in front of every later
    step.
    """
    for path, expected in [(home, "overlay")] + [(path, "tmpfs") for path in masks]:
        fstype, propagation = mount_facts(path)
        if fstype != expected:
            fail(f"{path} is on {fstype}, expected {expected}")
        if propagation != "private":
            fail(f"the mount at {path} is {propagation}, not private to this job")


def drop_privilege(sandbox: pwd.struct_passwd) -> list[str]:
    """The prefix that turns the root process building the view into the agent.

    Composed here, where the account has already been resolved, so the ids are
    numeric and nothing downstream has to look them up. ``exec`` all the way
    means the sandbox uid owns the same process the supervisor is waiting on,
    which is what the reap and the cancellation path already assume.

    The bounding set goes because nothing in the lifecycle execs a setuid
    binary, so it buys the sandbox nothing and costs it the one route to
    privilege that survives a uid change. bwrap is unaffected: creating a user
    namespace resets the bounding set inside it.
    """
    return [
        "/usr/bin/setpriv",
        f"--reuid={sandbox.pw_uid}",
        f"--regid={sandbox.pw_gid}",
        "--clear-groups",
        "--bounding-set=-all",
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--user", required=True)
    parser.add_argument("--mask", action="append", default=[], type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)

    command = options.command[1:] if options.command[:1] == ["--"] else options.command
    if not command:
        fail("no command to exec after building the view")
    if os.geteuid() != 0:
        fail("must run as root, under `unshare --mount --propagation private`")

    home = options.home.resolve(strict=True)
    sandbox = pwd.getpwnam(options.user)
    build_view(home=home, stage=options.stage, sandbox=sandbox)
    masks = [path.resolve(strict=False) for path in options.mask]
    for path in masks:
        mask(path, home=home)
    verify(home=home, masks=masks)
    # Re-resolve the working directory through the mounts just made: a cwd
    # inherited from the runner still points at the host's inode, which would
    # put the first reads of the lifecycle outside the view it is meant to run
    # in.
    os.chdir(home)
    argv = drop_privilege(sandbox) + command
    os.execv(argv[0], argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (KeyError, OSError, ValueError) as problem:
        print(f"::error::view: {problem}", flush=True)
        raise SystemExit(1) from None
