"""The id map that makes the agent's view of the job's home writable.

Everything else `enter_view` does needs a real kernel and a second uid, and is
covered by `proxy/test-setup-sandbox.sh` on a hosted runner. The map is the one
part that is arithmetic, and getting it wrong fails in two directions that both
look like something else: swap the columns and every write is EACCES, drop the
identity ranges and a create under a root-owned directory the consumer's
`setup:` left is EOVERFLOW.
"""

from __future__ import annotations

import pwd

import enter_view
import pytest


def parsed(entries: str, kind: str) -> dict[int, int]:
    """The map as `{id seen in the mount: id on disk}` for one id type."""
    seen: dict[int, int] = {}
    for entry in entries.split():
        entry_kind, mount, host, count = entry.split(":")
        if entry_kind != kind:
            continue
        for offset in range(int(count)):
            assert int(mount) + offset not in seen, f"{entry} overlaps an earlier range"
            seen[int(mount) + offset] = int(host) + offset
    return seen


def account(uid: int, gid: int) -> pwd.struct_passwd:
    return pwd.struct_passwd(("name", "x", uid, gid, "", "/home/name", "/bin/sh"))


def test_the_map_is_a_bijection_over_every_mappable_id() -> None:
    """No id is left out and none is mapped twice.

    An unmapped owner reads back as the kernel's overflow id, and overlayfs
    takes a copied-up inode's owner from the mapped lower stat — so a gap here
    is a directory the agent cannot write under, discovered mid-build.
    """
    table = parsed(enter_view.swap_map(account(1001, 1001), account(1002, 1002)), "u")

    assert sorted(table) == list(range(enter_view.ID_CEILING))
    assert sorted(table.values()) == list(range(enter_view.ID_CEILING))


def test_the_runner_reads_as_the_sandbox_and_the_reverse() -> None:
    """The swap itself, in both directions, and root left alone.

    Root is the case that matters beyond the pair: a directory `docker run -v`
    left behind is root-owned, and the agent has to be able to create under it.
    """
    table = parsed(enter_view.swap_map(account(1001, 1001), account(1002, 1002)), "u")

    assert table[1002] == 1001
    assert table[1001] == 1002
    assert table[0] == 0
    assert table[65534] == 65534


def test_the_map_does_not_assume_which_account_has_the_lower_id() -> None:
    """A self-hosted runner may have created `tend-sandbox` first."""
    lower_sandbox = parsed(
        enter_view.swap_map(account(1050, 1050), account(999, 999)), "u"
    )

    assert lower_sandbox[999] == 1050
    assert lower_sandbox[1050] == 999
    assert sorted(lower_sandbox) == list(range(enter_view.ID_CEILING))


def test_adjacent_accounts_emit_no_empty_range() -> None:
    """`mount` rejects a zero-length mapping, and adjacent ids are the norm."""
    entries = enter_view.swap_map(account(1001, 1001), account(1002, 1002))

    assert not any(entry.endswith(":0") for entry in entries.split())


def test_the_group_map_is_the_gids_and_not_the_uids() -> None:
    """A runner whose primary group id differs from its uid — the shape that
    would otherwise pass every test above and leave group-owned files
    unreachable."""
    entries = enter_view.swap_map(account(1001, 127), account(1002, 1002))

    assert parsed(entries, "g")[1002] == 127
    assert parsed(entries, "u")[1002] == 1001


def test_an_unmappable_account_is_refused_rather_than_truncated() -> None:
    with pytest.raises(ValueError, match="outside the mappable range"):
        enter_view.identity_map("u", 1001, enter_view.ID_CEILING)


def test_the_privilege_drop_keeps_nothing_the_agent_could_use() -> None:
    """`exec`ed, so the sandbox uid owns the process the supervisor waits on."""
    argv = enter_view.drop_privilege(account(1002, 1003))

    assert argv[0] == "/usr/bin/setpriv"
    assert "--reuid=1002" in argv
    assert "--regid=1003" in argv
    assert "--clear-groups" in argv
    assert "--bounding-set=-all" in argv
