"""Event-topology contracts for the checkout the agent works in.

The tree is the job's own, seen through the view, so these drive real Git
repositories rather than mocks: the step is now the only thing that moves HEAD
before the agent runs, and it moves the checkout a consumer's `setup:` already
built against.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path

import event_checkout
import pytest


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
    """The checkout is the job's own and outlives one topology selection, where
    the disposable clone this replaced was fresh every time. A `tend-head`
    remote left by an earlier selection must not fail the next one."""
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

    The fallback checks out the default branch, which is reviewed code. Pinning
    it to the base the PR opened against reverts every `CLAUDE.md`, `AGENTS.md`
    and `.claude/**` the branch has gained since — so the session runs on stale
    repo instructions, and the revert is staged by any later `git add -A`.
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
    """The dummy is what leaves this process; the proxy attaches the real one.

    The same route the agent's own pushes take, which is what moving the
    topology selection inside the sandbox bought.
    """
    captured: list[object] = []
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_tendproxydummy000000000000000000000")
    monkeypatch.setattr(
        event_checkout.urllib.request,
        "urlopen",
        lambda request, timeout: captured.append(request) or _json_response(),
    )

    event_checkout.api_json("/repos/owner/repo/pulls/7")

    request = captured[0]
    assert request.get_header("Authorization") == (
        "Bearer ghp_tendproxydummy000000000000000000000"
    )
    assert request.full_url.startswith("https://api.github.com/")


class _json_response:
    def read(self) -> bytes:
        return b"{}"

    def __enter__(self) -> _json_response:
        return self

    def __exit__(self, *_exception: object) -> None:
        return None
