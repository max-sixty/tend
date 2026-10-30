"""Event-topology contracts for the checkout the agent works in, against real
Git repositories."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from collections.abc import Callable
from pathlib import Path

import pytest
from tend.runtime.shared import event_checkout


def command(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """An origin, a checkout of its default branch, and a PR's head."""
    origin = tmp_path / "origin.git"
    command("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    command("init", "--initial-branch=main", str(seed), cwd=tmp_path)
    command("config", "user.email", "test@example.com", cwd=seed)
    command("config", "user.name", "Test", cwd=seed)
    (seed / "file").write_text("base\n")
    command("add", "file", cwd=seed)
    command("commit", "-m", "base", cwd=seed)
    base = command("rev-parse", "HEAD", cwd=seed)
    command("remote", "add", "origin", str(origin), cwd=seed)
    command("push", "-u", "origin", "main", cwd=seed)
    command("checkout", "-b", "feature", cwd=seed)
    (seed / "file").write_text("feature\n")
    command("commit", "-am", "feature", cwd=seed)
    head = command("rev-parse", "HEAD", cwd=seed)
    command("push", "origin", "feature", cwd=seed)
    command("push", "origin", f"{head}:refs/pull/7/head", cwd=seed)

    # What `actions/checkout` leaves: the base branch, and no credential.
    workspace = tmp_path / "workspace"
    command("clone", str(origin), str(workspace), cwd=tmp_path)
    command("config", "user.email", "bot@example.com", cwd=workspace)
    command("config", "user.name", "Bot", cwd=workspace)
    return origin, workspace, base, head


def test_review_takes_the_pull_request_ref_over_the_checked_out_base(
    tmp_path: Path,
) -> None:
    """`refs/pull/N/merge` first, then `/head` — the repository here has only
    the second, which is also what a conflicted PR leaves."""
    _origin, workspace, base, head = repository(tmp_path)

    selected = event_checkout.checkout_review(workspace, 7)

    assert selected == "PR #7 head ref"
    assert command("rev-parse", "HEAD", cwd=workspace) == head
    assert command("rev-parse", "HEAD", cwd=workspace) != base


def test_review_names_the_pull_request_when_neither_ref_exists(
    tmp_path: Path,
) -> None:
    _origin, workspace, _base, _head = repository(tmp_path)

    with pytest.raises(ValueError, match="neither merge nor head ref exists"):
        event_checkout.checkout_review(workspace, 99)


def test_mention_checks_out_the_api_head_with_a_push_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, workspace, base, head = repository(tmp_path)
    monkeypatch.setattr(
        event_checkout,
        "api_json",
        lambda _path: {
            "state": "open",
            "base": {"sha": base},
            "head": {
                "ref": "feature",
                "sha": head,
                "repo": {"clone_url": str(origin), "full_name": "fork/repo"},
            },
        },
    )

    selected, config_base = event_checkout.checkout_mention(
        workspace,
        repository="owner/repo",
        number=7,
        base_branch="main",
        base_sha=base,
    )

    assert selected == f"open PR #7 head fork/repo:feature at {head}"
    assert config_base == base
    assert command("rev-parse", "HEAD", cwd=workspace) == head
    assert command("rev-parse", "@{upstream}", cwd=workspace) == head


def test_a_second_mention_in_one_job_does_not_collide_on_the_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `tend-head` remote left by an earlier selection must not fail the next."""
    origin, workspace, base, head = repository(tmp_path)
    monkeypatch.setattr(
        event_checkout,
        "api_json",
        lambda _path: {
            "state": "open",
            "base": {"sha": base},
            "head": {
                "ref": "feature",
                "sha": head,
                "repo": {"clone_url": str(origin), "full_name": "fork/repo"},
            },
        },
    )
    arguments = {
        "repository": "owner/repo",
        "number": 7,
        "base_branch": "main",
        "base_sha": base,
    }

    event_checkout.checkout_mention(workspace, **arguments)
    event_checkout.checkout_mention(workspace, **arguments)

    assert command("rev-parse", "HEAD", cwd=workspace) == head


@pytest.mark.parametrize("sha", ["", "main", "-" * 40, "a" * 39, "a" * 41])
def test_pull_base_requires_an_exact_object_id(sha: str) -> None:
    with pytest.raises(ValueError, match="exact Git object ID"):
        event_checkout.pull_base_sha({"base": {"sha": sha}}, 7)


@pytest.mark.parametrize(
    ("payload", "number"),
    [
        ({"number": 1}, 1),
        ({"issue": {"number": 2}}, 2),
        ({"client_payload": {"pr": "3"}}, 3),
    ],
)
def test_event_number(payload: dict[str, object], number: int) -> None:
    assert event_checkout.event_number(payload) == number


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"issue": {"number": 1}}, False),
        ({"issue": {"number": 1, "pull_request": {"url": "example"}}}, True),
        ({"client_payload": {"pr": "3"}}, True),
        ({}, False),
    ],
)
def test_event_targets_pull_request(payload: dict[str, object], expected: bool) -> None:
    assert event_checkout.event_targets_pull_request(payload) is expected


def test_mention_on_a_closed_pr_keeps_the_default_branch_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A closed PR's base commit must not pin the default branch's own tree.

    The fallback checks out the default branch, whose instruction paths require
    maintainer approval. Pinning it to the base the PR opened against reverts
    every `CLAUDE.md`, `AGENTS.md` and `.claude/**` the branch has gained since.
    The session then runs on stale instructions, and any later `git add -A`
    stages the revert.
    """
    _origin, workspace, base, _head = repository(tmp_path)
    (workspace / "CLAUDE.md").write_text("current instructions\n")
    command("add", "CLAUDE.md", cwd=workspace)
    command("commit", "-m", "instructions", cwd=workspace)
    tip = command("rev-parse", "HEAD", cwd=workspace)
    command("push", "origin", "main", cwd=workspace)
    monkeypatch.setattr(
        event_checkout,
        "api_json",
        lambda _path: {"state": "closed", "base": {"sha": base}},
    )

    selected, config_base = event_checkout.checkout_mention(
        workspace,
        repository="owner/repo",
        number=7,
        base_branch="main",
        base_sha=tip,
    )

    assert selected == f"base branch main at {tip}"
    assert config_base == ""
    event_checkout.restore_sensitive_config(workspace, config_base)
    assert (workspace / "CLAUDE.md").read_text() == "current instructions\n"
    assert command("status", "--porcelain", cwd=workspace) == ""


def test_a_mention_on_an_issue_never_asks_the_pull_request_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `issues` event names an issue, and `/pulls/{number}` 404s on one.

    The handle job runs one mention checkout for every mention event it
    admits, `issues: edited` among them, so an issue number reaching the PR
    endpoint fails the step before the agent starts — losing every mention
    added by editing an issue body, silently from the thread's side.
    """
    _origin, workspace, base, _head = repository(tmp_path)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "edited", "issue": {"number": 7}}))

    def refuse(path: str) -> dict[str, object]:
        raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)

    monkeypatch.setattr(event_checkout, "api_json", refuse)
    for name, value in {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_EVENT_NAME": "issues",
        "TEND_CHECKOUT_MODE": "mention",
        "TEND_BASE_BRANCH": "main",
    }.items():
        monkeypatch.setenv(name, value)

    assert event_checkout.main() == 0
    assert command("rev-parse", "HEAD", cwd=workspace) == base
    assert command("rev-parse", "--abbrev-ref", "HEAD", cwd=workspace) == "main"


def test_the_api_call_holds_no_credential_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dummy is what leaves this process; the proxy attaches the real one."""
    captured: list[object] = []
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_tendproxydummy000000000000000000000")
    monkeypatch.setattr(
        event_checkout.urllib.request,
        "urlopen",
        lambda request, timeout: captured.append(request) or io.BytesIO(b"{}"),
    )

    event_checkout.api_json("/repos/owner/repo/pulls/7")

    request = captured[0]
    assert request.get_header("Authorization") == (
        "Bearer ghp_tendproxydummy000000000000000000000"
    )
    assert request.full_url.startswith("https://api.github.com/")


# Fork-PR instruction pinning. One tampered checkout exercises every shape a
# fork can give an instruction path — a rewrite, a move, a directory's name
# pointed outside the checkout (so a write or delete through it would land
# there), a directory swapped for a file and a file for a directory, and files
# or a `.claude` / `.agents` symlink planted where the base has none. The shared
# restoration step is held to the resulting exact end state.
_BASE = {
    "README.md": "base readme\n",
    "CLAUDE.md": "root instructions\n",
    "AGENTS.md": "-> CLAUDE.md",
    "AGENTS.override.md": "root override\n",
    ".agents/plugins/marketplace.json": "base plugins\n",
    ".agents/skills": "-> ../.claude/skills",
    ".claude/skills/running-tend/SKILL.md": "root skill\n",
    "site/CLAUDE.md": "site instructions\n",
    "docs/CLAUDE.md": "docs instructions\n",
    "docs/CLAUDE.local.md": "docs local instructions\n",
    "nested/AGENTS.md": "nested instructions\n",
    "services/AGENTS.override.md": "services override\n",
    "tools/CLAUDE.md": "tools instructions\n",
    "moved/CLAUDE.md": "moved instructions\n",
    "apps/api/.agents/skills/deploy/SKILL.md": "api skill\n",
    "apps/web/.claude/skills/deploy/SKILL.md": "web skill\n",
}

# Targets a fork symlink could redirect a write or a delete to.
_OUTSIDE = {
    rel: "must not change\n"
    for rel in (
        ".agents/skills/deploy/SKILL.md",
        ".claude/skills/deploy/SKILL.md",
        "CLAUDE.md",
        "AGENTS.md",
    )
}


def _write(path: Path, content: str) -> None:
    """Create *path* with *content*; `-> target` makes a symlink (the inverse
    of `_tree`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if content.startswith("-> "):
        path.unlink(missing_ok=True)
        path.symlink_to(content[3:])
    else:
        path.write_text(content)


def _tree(root: Path) -> dict[str, str]:
    """Every file under *root* by relative path: its text, or `-> target` for a
    symlink. Skips `.git/`."""
    tree: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] == ".git":
            continue
        if path.is_symlink():
            tree[str(rel)] = f"-> {path.readlink()}"
        elif path.is_file():
            tree[str(rel)] = path.read_text()
    return tree


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _fork_pr(
    tmp_path: Path,
    base: dict[str, str],
    tamper: Callable[[Path], None],
    base_ref: str = "main",
) -> tuple[Path, Path]:
    """A fork PR's checkout as `actions/checkout` leaves it: a clone of an
    origin holding *base*, with *tamper*'s changes committed on top and
    `origin/main` at the base. The event names *base_ref* as the PR's base.
    Returns (repo, event)."""
    origin = tmp_path / "origin"
    repo = tmp_path / "repo"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    for rel, content in base.items():
        _write(origin / rel, content)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", "base")
    _git(tmp_path, "clone", str(origin), str(repo))
    tamper(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fork tree")

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "base": {"ref": base_ref},
                    "head": {"repo": {"fork": True}},
                },
                "repository": {"default_branch": base_ref},
            }
        )
    )
    return repo, event


def _tampered_checkout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (repo, outside, event) for the scenario above."""
    outside = tmp_path / "outside"
    for rel, content in _OUTSIDE.items():
        _write(outside / rel, content)

    def tamper(repo: Path) -> None:
        _write(repo / "README.md", "fork readme\n")
        _write(repo / "CLAUDE.md", "EVIL root\n")
        _write(repo / "AGENTS.md", f"-> {outside / 'AGENTS.md'}")
        _write(repo / "AGENTS.override.md", "EVIL override\n")
        _write(repo / ".agents/plugins/marketplace.json", "EVIL plugins\n")
        (repo / ".agents/skills").unlink()
        _write(repo / ".agents/skills/fork-only/SKILL.md", "EVIL\n")
        _write(repo / ".claude/skills/running-tend/SKILL.md", "EVIL skill\n")
        _write(repo / ".claude/skills/fork-only/SKILL.md", "EVIL\n")
        _write(repo / ".claude/escape", f"-> {outside / 'CLAUDE.md'}")
        _write(repo / "CLAUDE.local.md", "EVIL\n")
        _write(repo / "AGENTS.override.md", "EVIL\n")
        _write(repo / "services/AGENTS.override.md", "EVIL services\n")
        _write(repo / "site/CLAUDE.md", "EVIL site\n")
        shutil.rmtree(repo / "docs")
        _write(repo / "docs", f"-> {outside}")
        shutil.rmtree(repo / "nested")
        _write(repo / "nested", "a file now\n")
        (repo / "tools/CLAUDE.md").unlink()
        _write(repo / "tools/CLAUDE.md/child", "EVIL\n")
        # Same content at a new path: git reports this as a rename.
        _write(repo / "elsewhere/CLAUDE.md", _BASE["moved/CLAUDE.md"])
        (repo / "moved/CLAUDE.md").unlink()
        shutil.rmtree(repo / "apps/api/.agents")
        _write(repo / "apps/api/.agents", f"-> {outside / '.agents'}")
        shutil.rmtree(repo / "apps/web")
        _write(repo / "apps/web", f"-> {outside}")
        _write(repo / "fork-only/CLAUDE.md", "EVIL\n")
        _write(repo / "fork-only/AGENTS.md", "EVIL\n")
        _write(repo / "fork-only/AGENTS.override.md", "EVIL\n")
        _write(repo / "notes/skills/deploy/SKILL.md", "EVIL\n")
        _write(repo / "site/.claude", "-> ../notes")
        _write(repo / "dir/CLAUDE.md/child", "not an instruction file\n")

    repo, event = _fork_pr(tmp_path, _BASE, tamper)
    return repo, outside, event


# The checkout after pinning: the base's instruction paths, the PR's own
# changes elsewhere, and fork content that isn't an instruction path — the
# directories planted `.claude` and `.agents` symlinks pointed at, and a
# directory named `CLAUDE.md`, which neither CLI can read as an instruction
# file.
_PINNED = {
    **_BASE,
    "README.md": "fork readme\n",
    "notes/skills/deploy/SKILL.md": "EVIL\n",
    "dir/CLAUDE.md/child": "not an instruction file\n",
}


def _pin(repo: Path, event: Path) -> None:
    payload = json.loads(event.read_text())
    base_ref = payload["pull_request"]["base"]["ref"]
    base = command("rev-parse", f"origin/{base_ref}", cwd=repo)
    event_checkout.restore_sensitive_config(repo, base)


def test_restore_sensitive_config_matches_base_for_every_instruction_path(
    tmp_path: Path,
) -> None:
    """Same contract under the Claude harness, plus its own: the revert stays
    out of the index so it can't ride into a commit the agent makes later. The
    end-state check also proves no copy of a fork file was left anywhere the
    agent can read, a copy made as the runner user having followed the fork's
    symlinks."""
    repo, outside, event = _tampered_checkout(tmp_path)

    _pin(repo, event)
    assert _tree(repo) == _PINNED
    assert _tree(outside) == _OUTSIDE
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert staged.stdout == "", staged.stdout


def test_restore_sensitive_config_pins_relayed_review_dispatch(
    tmp_path: Path,
) -> None:
    """A relayed review uses the base commit chosen by content ingress."""
    repo, outside, event = _tampered_checkout(tmp_path)
    event.write_text(json.dumps({"client_payload": {"pr": 7}}))
    base = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    event_checkout.restore_sensitive_config(repo, base)

    assert _tree(repo) == _PINNED
    assert _tree(outside) == _OUTSIDE


def test_pinning_leaves_a_pr_that_touches_no_instruction_path_alone(
    tmp_path: Path,
) -> None:
    """The usual PR: nothing the pin covers changed, so there is nothing to
    restore and the step still exits cleanly."""
    repo, event = _fork_pr(
        tmp_path,
        {"README.md": "base readme\n"},
        lambda repo: _write(repo / "README.md", "fork readme\n"),
    )

    _pin(repo, event)
    assert _tree(repo) == {"README.md": "fork readme\n"}


def test_pinning_fails_when_the_base_ref_is_missing(
    tmp_path: Path,
) -> None:
    """A base the checkout doesn't hold fails the step. The alternative, a diff
    against nothing that lists nothing, would let the agent start on the
    fork's instruction files with a log line saying 0 paths were pinned."""
    repo, _event = _fork_pr(
        tmp_path,
        {"CLAUDE.md": "root instructions\n"},
        lambda repo: _write(repo / "CLAUDE.md", "EVIL root\n"),
        base_ref="missing",
    )

    with pytest.raises(subprocess.CalledProcessError):
        event_checkout.restore_sensitive_config(repo, "0" * 40)


@pytest.mark.parametrize(
    "names",
    [
        ["bracket[*?]/CLAUDE.md", "line\r\nbreak/AGENTS.md"],
        pytest.param(
            [os.fsdecode(b"raw-\xff/CLAUDE.md")],
            marks=pytest.mark.skipif(
                sys.platform == "darwin",
                reason="macOS filesystem rejects non-UTF8 filenames",
            ),
        ),
    ],
)
def test_pinning_preserves_literal_path_bytes(tmp_path: Path, names: list[str]) -> None:
    # Glob syntax, line endings and undecodable bytes are legal Git filenames.
    base_tree = {name: "trusted\n" for name in names}

    def tamper(repo: Path) -> None:
        for name in names:
            _write(repo / name, "untrusted\n")
        _write(repo / "bracketx/CLAUDE.md", "also untrusted\n")

    repo, event = _fork_pr(tmp_path, base_tree, tamper)
    _pin(repo, event)

    assert _tree(repo) == base_tree
    assert command("diff", "--cached", "--name-only", cwd=repo) == ""
