"""Tests for land_pr.py — whether tend-land merges a reviewed PR at its commit.

`decide` is the whole of tend-land's judgement: every reason it declines is a
fact the review's record no longer covers, and the merge it allows is the
pinned one a review would make. A wrong `merge` lands code nobody read; a
wrong `skip` leaves a reviewed PR open, which a later session or maintainer
still lands.
"""

from __future__ import annotations

import importlib
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


def status(**overrides: Any) -> dict[str, Any]:
    return {
        "context": "tend/land",
        "state": "success",
        "creator": {"login": BOT},
        "created_at": RECORDED_AT,
        **overrides,
    }


def pr(**overrides: Any) -> dict[str, Any]:
    return {
        "number": 7,
        "state": "open",
        "draft": False,
        "user": {"login": "alice"},
        "head": {"sha": SHA},
        **overrides,
    }


def decide(**overrides: Any) -> tuple[str, str]:
    args: dict[str, Any] = {
        "pr": pr(),
        "sha": SHA,
        "bot": BOT,
        "landing": status(),
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
    assert decide(pr=pr(user={"login": BOT}), approval_sha="")[0] == "merge"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"landing": None}, "no landing recorded"),
        ({"pr": pr(head={"sha": "b" * 40})}, "no longer open"),
        ({"pr": pr(state="closed")}, "no longer open"),
        ({"pr": pr(draft=True)}, "draft"),
        ({"approval_sha": ""}, "approval no longer stands"),
        ({"approval_sha": "b" * 40}, "approval no longer stands"),
        ({"people": ["alice"]}, "alice wrote since"),
        ({"rollup": None}, "no complete check rollup"),
        ({"rollup": {**GREEN, "pending": ["ci / test"]}}, "still running"),
        ({"rollup": {**GREEN, "unverified": ["ci / test url"]}}, "without a result"),
        ({"rollup": {**GREEN, "failed": ["ci / test url"]}}, "red on"),
    ],
)
def test_anything_the_record_no_longer_covers_leaves_the_pr(
    overrides: dict[str, Any], reason: str
) -> None:
    decision, said = decide(**overrides)
    assert decision == "skip"
    assert reason in said


def test_a_red_on_the_bots_own_pr_is_handed_on_by_the_run_that_failed() -> None:
    """Each failing workflow's completion reads the same red; only a failed
    run hands it on, so a green sibling finishing later starts no second
    session."""
    red = {**GREEN, "failed": ["ci / test url"]}
    own = pr(user={"login": BOT})
    assert decide(pr=own, rollup=red, run_conclusion="failure")[0] == "red"
    assert decide(pr=own, rollup=red, run_conclusion="success")[0] == "skip"
    # Another author's red is theirs to see, as when a review ends on pending.
    assert decide(rollup=red, run_conclusion="failure")[0] == "skip"


def test_only_the_bots_latest_success_record_stands() -> None:
    later_error = status(state="error", created_at="2026-10-12T11:00:00Z")
    assert land_pr.record([later_error, status()], BOT) is None
    assert land_pr.record([status(creator={"login": "mallory"})], BOT) is None
    other = status(context="ci/test")
    assert land_pr.record([other, status()], BOT) == status()


def test_people_since_counts_only_people_after_the_record() -> None:
    activity = [
        {"user": {"login": "alice", "type": "User"}, "created_at": "2026-10-12T09:00:00Z"},
        {"user": {"login": BOT, "type": "User"}, "created_at": "2026-10-12T10:30:00Z"},
        {"user": {"login": "codecov", "type": "Bot"}, "created_at": "2026-10-12T10:30:00Z"},
        {"user": {"login": "bob", "type": "User"}, "submitted_at": "2026-10-12T10:31:00Z"},
        # A maintainer's bare approval adds no hold; one with words might.
        {
            "user": {"login": "carol", "type": "User"},
            "state": "APPROVED",
            "body": "",
            "submitted_at": "2026-10-12T10:32:00Z",
        },
        {
            "user": {"login": "dave", "type": "User"},
            "state": "APPROVED",
            "body": "LGTM once the docs land",
            "submitted_at": "2026-10-12T10:33:00Z",
        },
    ]
    assert land_pr.people_since(activity, since=RECORDED_AT, bot=BOT) == [
        "bob",
        "dave",
    ]
