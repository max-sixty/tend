"""Tests for land_pr.py — the records that carry a landing past the review.

`decide` is tend-land's whole agentless judgement: whether a CI completion
starts a landing session (a recorded head on its recorded base, every check
green), a repair session (red, no human author), or nothing. It reads no
prose; holds and approval are the landing session's. `intend` decides which
heads get a record at all.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "plugins" / "tend-ci-runner" / "scripts"
sys.path.insert(0, str(SCRIPTS))
land_pr = importlib.import_module("land_pr")

BOT = "tend-bot"
SHA = "a" * 40
GREEN: dict[str, list[str]] = {"pending": [], "failed": [], "unverified": []}
RED = {**GREEN, "failed": ["ci / test url"]}
OWN = {"login": BOT, "type": "User"}
DEPENDABOT = {"login": "dependabot[bot]", "type": "Bot"}


def status(context: str = "tend/land/7", **overrides: Any) -> dict[str, Any]:
    return {
        "context": context,
        "state": "success",
        "creator": {"login": BOT},
        "description": json.dumps({"base": "main", "skip": []}),
        **overrides,
    }


def landing(**overrides: Any) -> dict[str, Any]:
    return {**status(), "base": "main", "skip": frozenset(), **overrides}


def pr(**overrides: Any) -> dict[str, Any]:
    return {
        "number": 7,
        "user": {"login": "alice", "type": "User"},
        "head": {"sha": SHA},
        "base": {"ref": "main"},
        **overrides,
    }


def decide(**overrides: Any) -> tuple[str, str]:
    args: dict[str, Any] = {
        "pr": pr(),
        "sha": SHA,
        "bot": BOT,
        "landing": landing(),
        "rollup": GREEN,
        "run_conclusion": "success",
        **overrides,
    }
    return land_pr.decide(**args)


def test_a_recorded_head_on_its_base_with_every_check_green_starts_landing() -> None:
    assert decide()[0] == "land"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"landing": None}, "no landing recorded"),
        ({"pr": pr(base={"ref": "release"})}, "no landing recorded"),
        ({"pr": pr(head={"sha": "b" * 40})}, "no longer at"),
        ({"rollup": None}, "no complete check rollup"),
        ({"rollup": {**GREEN, "pending": ["ci / test"]}}, "still running"),
        ({"rollup": {**GREEN, "unverified": ["ci / test url"]}}, "without a result"),
        ({"rollup": RED}, "red on"),
    ],
)
def test_anything_short_of_a_green_recorded_head_starts_no_landing(
    overrides: dict[str, Any], reason: str
) -> None:
    decision, said = decide(**overrides)
    assert decision == "skip"
    assert reason in said


@pytest.mark.parametrize("author", [OWN, DEPENDABOT], ids=["self", "dependabot"])
@pytest.mark.parametrize("recorded", [True, False], ids=["recorded", "unrecorded"])
def test_a_red_on_a_pr_with_no_human_author_starts_its_repair(
    author: dict[str, str], recorded: bool
) -> None:
    """The review that waited repaired these; with no session waiting, the
    run that failed hands it on, record or not. A green sibling finishing
    later reads the same failure and starts nothing."""
    authored = pr(user=author)
    held = landing() if recorded else None
    red = decide(pr=authored, landing=held, rollup=RED, run_conclusion="failure")
    assert red[0] == "red"
    later = decide(pr=authored, landing=held, rollup=RED, run_conclusion="success")
    assert later[0] == "skip"


def test_a_human_authors_red_is_theirs() -> None:
    assert decide(rollup=RED, run_conclusion="failure")[0] == "skip"


def test_a_failure_beside_running_checks_is_handed_on_by_its_own_run() -> None:
    red_running = {**RED, "pending": ["lint"]}
    assert (
        decide(pr=pr(user=OWN), rollup=red_running, run_conclusion="failure")[0]
        == "red"
    )


def test_a_record_belongs_to_one_pr() -> None:
    """Two PRs can share a head commit; the review of #7 records only #7."""
    statuses = [status()]
    assert land_pr.record(statuses, BOT, 7) is not None
    assert land_pr.record(statuses, BOT, 8) is None


def test_only_the_bots_latest_well_formed_success_record_stands() -> None:
    assert land_pr.record([status(state="error"), status()], BOT, 7) is None
    assert land_pr.record([status(creator={"login": "mallory"})], BOT, 7) is None
    assert land_pr.record([status(description="Reviewed")], BOT, 7) is None
    found = land_pr.record([status(context="ci/test"), status()], BOT, 7)
    assert found is not None
    assert (found["base"], found["skip"]) == ("main", frozenset())


def test_the_record_carries_the_reviews_skipped_checks() -> None:
    policy = json.dumps({"base": "main", "skip": ["bench"]})
    found = land_pr.record([status(description=policy)], BOT, 7)
    assert found is not None
    assert found["skip"] == frozenset({"bench"})


class FakeGitHub:
    """`github_cli` as `intend` reaches it: canned reads, recorded writes."""

    def __init__(self) -> None:
        self.view = {"headRefOid": SHA, "state": "OPEN", "baseRefName": "main"}
        self.statuses = [status(context="tend/land/pushed", description="Pushed")]
        self.writes: list[tuple[str, ...]] = []

    def json_call(self, *args: str, **_: Any) -> Any:
        return {"login": BOT} if args[:2] == ("api", "user") else self.view

    def paginated(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        return self.statuses

    def run(self, *args: str, **_: Any) -> str:
        self.writes.append(args)
        return ""


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(land_pr.github_cli, "json_call", fake.json_call)
    monkeypatch.setattr(land_pr.github_cli, "paginated", fake.paginated)
    monkeypatch.setattr(land_pr.github_cli, "run", fake.run)
    monkeypatch.setattr(land_pr.poll_pr_checks, "poll", lambda *_, **__: 2)
    return fake


def _fields(write: tuple[str, ...]) -> dict[str, str]:
    return dict(arg.split("=", 1) for arg in write if "=" in arg)


def test_intend_records_pr_reviewed_base_and_skipped_checks(
    github: FakeGitHub,
) -> None:
    assert land_pr.intend("7", SHA, "main", skip=frozenset({"bench"})) == 2
    (write,) = github.writes
    fields = _fields(write)
    assert fields["context"] == "tend/land/7"
    assert json.loads(fields["description"]) == {"base": "main", "skip": ["bench"]}


def test_intend_leaves_a_head_with_no_record_of_the_bots_push(
    github: FakeGitHub,
) -> None:
    """Who pushed is known to the session that pushed, and only the bot can
    make a status the bot created; commit metadata says neither."""
    github.statuses = []
    assert land_pr.intend("7", SHA, "main") == 3
    github.statuses = [status(context="tend/land/pushed", creator={"login": "eve"})]
    assert land_pr.intend("7", SHA, "main") == 3
    assert github.writes == []


@pytest.mark.parametrize(
    "view",
    [
        {"headRefOid": "b" * 40, "state": "OPEN", "baseRefName": "main"},
        {"headRefOid": SHA, "state": "CLOSED", "baseRefName": "main"},
        # Retargeted between the review and the record.
        {"headRefOid": SHA, "state": "OPEN", "baseRefName": "release"},
    ],
)
def test_intend_refuses_a_pr_that_moved_since_the_review(
    github: FakeGitHub, view: dict[str, str]
) -> None:
    github.view = view
    assert land_pr.intend("7", SHA, "main") == 3
    assert github.writes == []


def test_intend_refuses_a_policy_the_record_cannot_hold(github: FakeGitHub) -> None:
    skip = frozenset(f"a long advisory check name {n}" for n in range(6))
    assert land_pr.intend("7", SHA, "main", skip=skip) == 3
    assert github.writes == []


def test_pushed_records_the_bots_push(github: FakeGitHub) -> None:
    assert land_pr.pushed(SHA) == 0
    (write,) = github.writes
    assert _fields(write)["context"] == "tend/land/pushed"
    assert f"repos/owner/repo/statuses/{SHA}" in write
