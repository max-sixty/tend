"""Tests for land_pr.py — whether tend-land merges a reviewed PR at its commit.

`decide` is the whole of tend-land's judgement: every reason it declines is a
fact the review's record no longer covers, and the merge it allows is the
pinned one a review would make. A wrong `merge` lands code nobody read; a
wrong `skip` leaves a reviewed PR open, which a later session or maintainer
still lands. `intend` decides which heads get a record at all.
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
RECORDED_AT = "2026-10-12T10:00:00Z"
GREEN: dict[str, list[str]] = {"pending": [], "failed": [], "unverified": []}
RED = {**GREEN, "failed": ["ci / test url"]}


def status(number: int = 7, **overrides: Any) -> dict[str, Any]:
    return {
        "context": f"tend/land/{number}",
        "state": "success",
        "creator": {"login": BOT},
        "created_at": RECORDED_AT,
        "description": json.dumps({"base": "main", "skip": []}),
        **overrides,
    }


def landing(**overrides: Any) -> dict[str, Any]:
    return {**status(), "base": "main", "skip": frozenset(), **overrides}


def pr(**overrides: Any) -> dict[str, Any]:
    return {
        "number": 7,
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "type": "User"},
        "head": {"sha": SHA},
        "base": {"ref": "main"},
        **overrides,
    }


OWN = {"login": BOT, "type": "User"}
DEPENDABOT = {"login": "dependabot[bot]", "type": "Bot"}


def decide(**overrides: Any) -> tuple[str, str]:
    args: dict[str, Any] = {
        "pr": pr(),
        "sha": SHA,
        "bot": BOT,
        "landing": landing(),
        "approval_sha": SHA,
        "people": [],
        "rollup": GREEN,
        "run_conclusion": "success",
        **overrides,
    }
    return land_pr.decide(**args)


def test_a_recorded_approved_green_head_merges() -> None:
    assert decide()[0] == "merge"


def test_the_bots_own_pr_needs_no_approval() -> None:
    """GitHub refuses a self-approval, so the record alone is the review's."""
    assert decide(pr=pr(user=OWN), approval_sha="")[0] == "merge"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"landing": None}, "no landing recorded"),
        ({"pr": pr(head={"sha": "b" * 40})}, "no longer open"),
        ({"pr": pr(state="closed")}, "no longer open"),
        ({"pr": pr(base={"ref": "release"})}, "retargeted to release"),
        ({"pr": pr(draft=True)}, "draft"),
        ({"approval_sha": ""}, "approval no longer stands"),
        ({"approval_sha": "b" * 40}, "approval no longer stands"),
        ({"people": ["alice"]}, "alice wrote since"),
        ({"rollup": None}, "no complete check rollup"),
        ({"rollup": {**GREEN, "pending": ["ci / test"]}}, "still running"),
        ({"rollup": {**GREEN, "unverified": ["ci / test url"]}}, "without a result"),
        ({"rollup": RED}, "red on"),
    ],
)
def test_anything_the_record_no_longer_covers_leaves_the_pr(
    overrides: dict[str, Any], reason: str
) -> None:
    decision, said = decide(**overrides)
    assert decision == "skip"
    assert reason in said


@pytest.mark.parametrize("author", [OWN, DEPENDABOT], ids=["self", "dependabot"])
def test_a_red_on_a_pr_with_no_human_author_is_handed_on_by_the_run_that_failed(
    author: dict[str, str],
) -> None:
    """Each failing workflow's completion reads the same red; only a failed
    run hands it on, so a green sibling finishing later starts no second
    session. A Dependabot PR is repaired as the bot's own is: the review was
    its author too."""
    authored = pr(user=author)
    assert decide(pr=authored, rollup=RED, run_conclusion="failure")[0] == "red"
    assert decide(pr=authored, rollup=RED, run_conclusion="success")[0] == "skip"


def test_a_human_authors_red_is_theirs() -> None:
    assert decide(rollup=RED, run_conclusion="failure")[0] == "skip"


def test_a_failure_beside_running_checks_is_handed_on_by_its_own_run() -> None:
    """A failed check is final. Waiting for its siblings would leave it to a
    green completion, which hands nothing on, so no repair would start."""
    red_running = {**RED, "pending": ["lint"]}
    own = pr(user=OWN)
    assert decide(pr=own, rollup=red_running, run_conclusion="failure")[0] == "red"


def test_a_record_belongs_to_one_pr() -> None:
    """Two PRs can share a head commit; the review of #7 records only #7."""
    statuses = [status(number=7)]
    assert land_pr.record(statuses, BOT, 7) is not None
    assert land_pr.record(statuses, BOT, 8) is None


def test_only_the_bots_latest_well_formed_success_record_stands() -> None:
    later_error = status(state="error", created_at="2026-10-12T11:00:00Z")
    assert land_pr.record([later_error, status()], BOT, 7) is None
    assert land_pr.record([status(creator={"login": "mallory"})], BOT, 7) is None
    assert land_pr.record([status(description="Reviewed")], BOT, 7) is None
    other = status(context="ci/test")
    found = land_pr.record([other, status()], BOT, 7)
    assert found is not None
    assert (found["base"], found["skip"]) == ("main", frozenset())


def test_the_record_carries_the_reviews_skipped_checks() -> None:
    policy = json.dumps({"base": "main", "skip": ["bench"]})
    found = land_pr.record([status(description=policy)], BOT, 7)
    assert found is not None
    assert found["skip"] == frozenset({"bench"})


def test_people_since_counts_writing_edits_and_ties_after_the_record() -> None:
    def user(login: str, kind: str = "User") -> dict[str, str]:
        return {"login": login, "type": kind}

    activity = [
        {"user": user("alice"), "created_at": "2026-10-12T09:00:00Z"},
        # Written before the record, edited into a hold after it.
        {
            "user": user("erin"),
            "created_at": "2026-10-12T09:00:00Z",
            "updated_at": "2026-10-12T10:05:00Z",
        },
        # Same second as the record: which came first is unknown.
        {"user": user("frank"), "created_at": RECORDED_AT},
        {"user": user(BOT), "created_at": "2026-10-12T10:30:00Z"},
        {"user": user("codecov", "Bot"), "created_at": "2026-10-12T10:30:00Z"},
        {"user": user("bob"), "submitted_at": "2026-10-12T10:31:00Z"},
        # A maintainer's bare approval adds no hold; one with words might.
        {
            "user": user("carol"),
            "state": "APPROVED",
            "body": "",
            "submitted_at": "2026-10-12T10:32:00Z",
        },
        {
            "user": user("dave"),
            "state": "APPROVED",
            "body": "LGTM once the docs land",
            "submitted_at": "2026-10-12T10:33:00Z",
        },
    ]
    assert land_pr.people_since(activity, since=RECORDED_AT, bot=BOT) == [
        "erin",
        "frank",
        "bob",
        "dave",
    ]


class FakeGitHub:
    """`github_cli` as `intend` reaches it: canned reads, recorded writes."""

    def __init__(self, committer: str) -> None:
        self.committer = committer
        self.writes: list[tuple[str, ...]] = []

    def json_call(self, *args: str, **_: Any) -> Any:
        if args[:2] == ("api", "user"):
            return {"login": BOT}
        if args[0] == "pr":
            return {"headRefOid": SHA, "state": "OPEN", "baseRefName": "main"}
        return {"committer": {"login": self.committer}}

    def run(self, *args: str, **_: Any) -> str:
        self.writes.append(args)
        return ""


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub(committer=BOT)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(land_pr.github_cli, "json_call", fake.json_call)
    monkeypatch.setattr(land_pr.github_cli, "run", fake.run)
    monkeypatch.setattr(land_pr.poll_pr_checks, "poll", lambda *_, **__: 2)
    return fake


def test_intend_records_pr_base_and_skipped_checks(github: FakeGitHub) -> None:
    assert land_pr.intend("7", SHA, skip=frozenset({"bench"})) == 2
    (write,) = github.writes
    fields = dict(arg.split("=", 1) for arg in write if "=" in arg)
    assert fields["context"] == "tend/land/7"
    assert json.loads(fields["description"]) == {"base": "main", "skip": ["bench"]}


def test_intend_leaves_a_head_someone_else_pushed(github: FakeGitHub) -> None:
    github.committer = "alice"
    assert land_pr.intend("7", SHA) == 3
    assert github.writes == []


def test_intend_refuses_a_policy_the_record_cannot_hold(github: FakeGitHub) -> None:
    skip = frozenset(f"a long advisory check name {n}" for n in range(6))
    assert land_pr.intend("7", SHA, skip=skip) == 3
    assert github.writes == []
