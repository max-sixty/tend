from __future__ import annotations

import subprocess

import pytest
import security_preflight
from _fakes import FakeGh

REPO = "owner/repo"


@pytest.fixture(autouse=True)
def actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)


def _repo(fake_gh: FakeGh, *, rules: object, protected: bool | None = None) -> None:
    """Answer the default-branch lookup, the branch's rules, and `.protected`.

    ``rules`` is the one page of the listing, an ``int`` to make that call fail,
    or a string to answer it with a body that is not JSON.
    """
    fake_gh.respond("api", f"repos/{REPO}", with_={"default_branch": "main"})
    fake_gh.respond(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{REPO}/rules/branches/main",
        with_=[rules] if isinstance(rules, list) else rules,
    )
    if protected is not None:
        fake_gh.respond(
            "api", f"repos/{REPO}/branches/main", with_={"protected": protected}
        )


def _update_rule(ruleset_id: int) -> dict[str, object]:
    return {"type": "update", "ruleset_id": ruleset_id}


def _bypass(fake_gh: FakeGh, ruleset_id: int, answer: object) -> None:
    """GitHub's answer to "can this bot bypass ruleset *ruleset_id*?"."""
    fake_gh.respond(
        "api",
        f"repos/{REPO}/rulesets/{ruleset_id}",
        with_={"current_user_can_bypass": answer},
    )


def test_update_ruleset_ids_keeps_update_rules_once() -> None:
    """One ruleset contributing several rules to a branch is queried once."""
    rules = [
        {"type": "pull_request", "ruleset_id": 1},
        _update_rule(7),
        {"type": "required_signatures", "ruleset_id": 7},
        _update_rule(7),
        _update_rule(3),
    ]
    assert security_preflight.update_ruleset_ids(rules) == [3, 7]


def test_update_ruleset_ids_ignores_entries_that_are_not_rules() -> None:
    """The jq `select` this replaced dropped these; nothing may raise on one."""
    assert security_preflight.update_ruleset_ids(
        [{"ruleset_id": 1}, {"type": "update"}, "not a rule", _update_rule(4)]
    ) == [4]


def test_an_update_rule_past_the_first_page_counts(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """The listing serves 30 rules a page."""
    first = [{"type": "required_signatures", "ruleset_id": 2}] * 30
    _repo(fake_gh, rules=1)
    fake_gh.respond(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{REPO}/rules/branches/main",
        with_=[first, [_update_rule(1)]],
    )
    _bypass(fake_gh, 1, "never")

    assert security_preflight.main() == 0
    assert "Security preflight passed: bot cannot bypass" in capsys.readouterr().out


def test_a_page_answered_with_an_error_object_falls_back_to_the_floor(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A page GitHub served as an error object leaves the listing unread, not
    short: a short one would read as "no update rule" and abort the gate."""
    first = [{"type": "required_signatures", "ruleset_id": 2}] * 30
    _repo(fake_gh, rules=1, protected=True)
    fake_gh.respond(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{REPO}/rules/branches/main",
        with_=[first, {"message": "502"}],
    )

    assert security_preflight.main() == 0
    assert "default branch 'main' is protected" in capsys.readouterr().out


def test_a_listing_of_entries_that_are_not_rules_aborts(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A listing that reads cleanly is GitHub's answer, even when no entry in it
    is a usable update rule; only an unread listing reaches the floor."""
    _repo(fake_gh, rules=[{"type": "update"}, "not a rule"])

    assert security_preflight.main() == 1
    assert "::error::No restrict-updates ruleset covers 'main'." in (
        capsys.readouterr().out
    )


def test_a_rules_listing_that_is_not_json_falls_back_to_the_protected_floor(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A GitHub blip answers a 200 with an HTML page: the parse fails, not the call.

    Catching only the non-zero exit takes the gate down on an outage — and
    "Report failure" keys on this step's outcome, so that outage would go
    unrecorded on top of it.
    """
    _repo(fake_gh, rules="<html>502 Bad Gateway</html>", protected=True)

    assert security_preflight.main() == 0
    assert "default branch 'main' is protected" in capsys.readouterr().out


def test_passes_when_one_update_ruleset_cannot_be_bypassed(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single `never` proves the bot cannot update the branch.

    It is reached past a bypassable ruleset, so the verdict has to survive an
    earlier `bypassable`, and the `.protected` floor is never consulted — the
    fake `gh` has no answer for it, so a call would fail the test.
    """
    _repo(fake_gh, rules=[_update_rule(1), _update_rule(2)])
    _bypass(fake_gh, 1, "pull_requests_only")
    _bypass(fake_gh, 2, "never")

    assert security_preflight.main() == 0
    assert "Security preflight passed: bot cannot bypass" in capsys.readouterr().out


def test_aborts_when_every_update_ruleset_is_bypassable(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(fake_gh, rules=[_update_rule(1)])
    _bypass(fake_gh, 1, "always")

    assert security_preflight.main() == 1
    assert (
        "::error::The bot can bypass every restrict-updates ruleset on 'main' "
        "(current_user_can_bypass != never)" in capsys.readouterr().out
    )


def test_an_unreadable_ruleset_falls_back_to_the_protected_floor(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ruleset the token cannot read proves nothing, so it is not bypassable.

    Counting it as bypassable would abort every run in a repo whose rulesets
    are org-owned and unreadable to the bot; counting it as blocked would let
    an unrestricted branch through. Neither — the `.protected` floor decides.
    """
    _repo(fake_gh, rules=[_update_rule(1)], protected=True)
    fake_gh.respond("api", f"repos/{REPO}/rulesets/1", with_=1)

    assert security_preflight.main() == 0
    assert (
        "Security preflight passed: default branch 'main' is protected"
        in capsys.readouterr().out
    )


def test_aborts_on_a_branch_protected_by_required_reviews_alone(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """Required reviews contribute no update rule, and don't restrict the bot.

    Its own approval counts on a PR someone else opened, so a readable listing
    with no update rule settles it: the `.protected` floor is never consulted,
    and the fake `gh` has no answer for it.
    """
    _repo(fake_gh, rules=[{"type": "pull_request", "ruleset_id": 1}])

    assert security_preflight.main() == 1
    assert (
        "::error::No restrict-updates ruleset covers 'main'." in capsys.readouterr().out
    )


def test_an_unreadable_rules_listing_falls_back_to_the_protected_floor(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token that cannot list a branch's rules still has to clear the floor."""
    _repo(fake_gh, rules=1, protected=False)

    assert security_preflight.main() == 1
    assert "::error::Default branch 'main' is NOT protected." in capsys.readouterr().out


def test_refuses_to_run_without_the_repository(
    monkeypatch: pytest.MonkeyPatch, fake_gh: FakeGh
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "")
    with pytest.raises(SystemExit, match="GITHUB_REPOSITORY"):
        security_preflight.main()
    assert fake_gh.calls == []


@pytest.mark.parametrize(
    "failing", ["", "/branches/main"], ids=["default-branch", "protected"]
)
def test_surfaces_githubs_own_error_when_a_required_call_fails(
    fake_gh: FakeGh, failing: str
) -> None:
    """A read the gate cannot do without is left to raise, never swallowed.

    `_common.run` turns it into the step's one `::error::`, with gh's own
    "Bad credentials" / "Not Found" already relayed to stderr by `_common.gh` —
    the whole diagnosis for a misconfigured install, which this gate is the
    step most likely to meet.
    """
    _repo(fake_gh, rules=1, protected=True)
    fake_gh.respond("api", f"repos/{REPO}{failing}", with_=1)

    with pytest.raises(subprocess.CalledProcessError):
        security_preflight.main()
