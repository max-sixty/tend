"""The id map and environment behind the agent's view; the mounts are covered
by `proxy/test-setup-sandbox.sh` on a hosted runner."""

from __future__ import annotations

import pwd
from pathlib import Path

import enter_view
import pytest


def parsed(entries: str, kind: str) -> dict[int, int]:
    """The map as `{id seen in the mount: id on disk}` for one id type."""
    seen: dict[int, int] = {}
    for entry in entries.split():
        entry_kind, mount, host, count = entry.split(":")
        assert int(count) > 0, f"{entry} is an empty range, which mount rejects"
        if entry_kind != kind:
            continue
        for offset in range(int(count)):
            assert int(mount) + offset not in seen, f"{entry} overlaps an earlier range"
            seen[int(mount) + offset] = int(host) + offset
    return seen


def account(uid: int, gid: int) -> pwd.struct_passwd:
    return pwd.struct_passwd(("name", "x", uid, gid, "", "/home/name", "/bin/sh"))


@pytest.mark.parametrize(
    ("runner", "sandbox"),
    [
        (account(1001, 1001), account(1002, 1002)),
        # The sandbox account created first, with the lower id.
        (account(1050, 1050), account(999, 999)),
        # A primary group that differs from the uid.
        (account(1001, 127), account(1002, 1002)),
        # `useradd -g`: both accounts share one group, so there is nothing to swap.
        (account(1001, 1000), account(1002, 1000)),
    ],
)
def test_the_map_swaps_the_two_accounts_and_is_identity_elsewhere(
    runner: pwd.struct_passwd, sandbox: pwd.struct_passwd
) -> None:
    entries = enter_view.swap_map(runner, sandbox)
    for kind, ours, theirs in (
        ("u", runner.pw_uid, sandbox.pw_uid),
        ("g", runner.pw_gid, sandbox.pw_gid),
    ):
        table = parsed(entries, kind)
        swapped = {ours: theirs, theirs: ours}
        assert table == {
            id_: swapped.get(id_, id_) for id_ in range(enter_view.ID_CEILING)
        }


def test_an_unmappable_account_is_refused_rather_than_truncated() -> None:
    with pytest.raises(ValueError, match="outside the mappable range"):
        enter_view.identity_map("u", 1001, enter_view.ID_CEILING)


def test_the_environment_file_is_read_whole_and_then_removed(tmp_path: Path) -> None:
    """NUL-separated, so a value keeps its `=` and newlines; the last entry wins.

    Removed at once: it holds the job's whole environment, and nothing reads it
    after the exec.
    """
    env_file = tmp_path / "tend-launch-env"
    env_file.write_bytes(
        b"\0".join(
            [b"HOME=/home/tend-sandbox", b"NOTE=a=b\nc", b"HOME=/home/runner", b""]
        )
    )

    environment = enter_view.read_environment(env_file)

    assert environment == {b"HOME": b"/home/runner", b"NOTE": b"a=b\nc"}
    assert not env_file.exists()


def test_a_symlinked_environment_file_is_refused(tmp_path: Path) -> None:
    """Root reads it, so a link would hand the agent any file root can read."""
    secret = tmp_path / "root-only"
    secret.write_bytes(b"SECRET=root-only")
    link = tmp_path / "tend-launch-env"
    link.symlink_to(secret)

    with pytest.raises(OSError):
        enter_view.read_environment(link)
