from __future__ import annotations

import base64
import subprocess

import pytest
import security_preflight
from _fakes import FakeGh
from tend.workflows import codeowners_config

REPO = "owner/repo"


def _generated_codeowners(existing: str | None = None) -> str:
    """What `tend init` writes, so the preflight's copy of the block is checked
    against the generator's rather than against a third copy here."""
    content = codeowners_config(existing, "@octocat")
    assert content is not None
    return content


@pytest.fixture(autouse=True)
def actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("TEND_MERGE", "restricted")
    monkeypatch.setenv("TEND_CONTROL_PLANE_OWNER", "@octocat")


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


def _lifecycle_rules(ruleset_id: int) -> list[dict[str, object]]:
    return [
        {"type": "creation", "ruleset_id": ruleset_id},
        _update_rule(ruleset_id),
        {"type": "deletion", "ruleset_id": ruleset_id},
    ]


def _bypass(fake_gh: FakeGh, ruleset_id: int, answer: object) -> None:
    """GitHub's answer to "can this bot bypass ruleset *ruleset_id*?"."""
    fake_gh.respond(
        "api",
        f"repos/{REPO}/rulesets/{ruleset_id}",
        with_={"current_user_can_bypass": answer},
    )


def _codeowners(fake_gh: FakeGh) -> None:
    fake_gh.respond(
        "api",
        "graphql",
        with_={
            "data": {
                "repository": {
                    "object": {"entries": [{"name": "CODEOWNERS", "mode": 0o100644}]}
                }
            }
        },
    )
    content = _generated_codeowners()
    fake_gh.respond(
        "api",
        f"repos/{REPO}/contents/.github/CODEOWNERS?ref=main",
        with_={"content": base64.b64encode(content.encode()).decode()},
    )
    fake_gh.respond(
        "api", f"repos/{REPO}/codeowners/errors?ref=main", with_={"errors": []}
    )
    fake_gh.respond("api", "user", with_={"login": "tend-bot"})


def test_control_plane_codeowners_does_not_skip_an_unreadable_higher_priority_file(
    monkeypatch: pytest.MonkeyPatch, fake_gh: FakeGh
) -> None:
    monkeypatch.setenv("TEND_MERGE", "yolo")
    fake_gh.respond("api", f"repos/{REPO}", with_={"default_branch": "main"})
    fake_gh.respond(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{REPO}/rules/branches/main",
        with_=[_lifecycle_rules(1)],
    )
    fake_gh.respond(
        "api", f"repos/{REPO}/contents/.github/CODEOWNERS?ref=main", with_=1
    )
    _bypass(fake_gh, 1, "pull_requests_only")
    fake_gh.respond("api", "user", with_={"login": "tend-bot"})

    assert security_preflight.main() == 1
    assert not fake_gh.called("api", f"repos/{REPO}/contents/CODEOWNERS?ref=main")


def test_control_plane_codeowners_falls_through_an_absent_higher_priority_file(
    fake_gh: FakeGh,
) -> None:
    fake_gh.respond(
        "api",
        "graphql",
        with_={
            "data": {
                "repository": {
                    "object": {"entries": [{"name": "CODEOWNERS", "mode": 0o100644}]}
                }
            }
        },
    )
    content = _generated_codeowners()

    def not_found(args: tuple[str, ...], stdin: str | None) -> str:
        raise subprocess.CalledProcessError(
            1, ["gh", *args], "", "gh: Not Found (HTTP 404)"
        )

    fake_gh.respond(
        "api",
        f"repos/{REPO}/contents/.github/CODEOWNERS?ref=main",
        with_=not_found,
    )
    fake_gh.respond(
        "api",
        f"repos/{REPO}/contents/CODEOWNERS?ref=main",
        with_={"content": base64.b64encode(content.encode()).decode()},
    )
    fake_gh.respond(
        "api", f"repos/{REPO}/codeowners/errors?ref=main", with_={"errors": []}
    )

    assert security_preflight.has_valid_control_plane_codeowners(
        REPO, "main", "@octocat"
    )


def test_control_plane_codeowners_accepts_the_generated_block_after_consumer_rules(
    fake_gh: FakeGh,
) -> None:
    _codeowners(fake_gh)
    content = _generated_codeowners("* @someone-else\n/docs/ @docs-team\n")
    fake_gh.respond(
        "api",
        f"repos/{REPO}/contents/.github/CODEOWNERS?ref=main",
        with_={"content": base64.b64encode(content.encode()).decode()},
    )
    assert security_preflight.has_valid_control_plane_codeowners(
        REPO, "main", "@octocat"
    )


def test_control_plane_codeowners_rejects_dereferenced_symlink(fake_gh: FakeGh) -> None:
    _codeowners(fake_gh)
    fake_gh.respond(
        "api",
        "graphql",
        with_={
            "data": {
                "repository": {
                    "object": {"entries": [{"name": "CODEOWNERS", "mode": 0o120000}]}
                }
            }
        },
    )
    assert not security_preflight.has_valid_control_plane_codeowners(
        REPO, "main", "@octocat"
    )


def test_ruleset_ids_keeps_update_rules_once() -> None:
    """One ruleset contributing several rules to a branch is queried once."""
    rules = [
        {"type": "pull_request", "ruleset_id": 1},
        _update_rule(7),
        {"type": "required_signatures", "ruleset_id": 7},
        _update_rule(7),
        _update_rule(3),
    ]
    assert security_preflight.ruleset_ids(rules, "update") == [3, 7]


def test_ruleset_ids_ignores_a_body_it_cannot_read_as_rules() -> None:
    """The jq `select` this replaced dropped these; nothing may raise on one.

    The listing is read best-effort, so an error object under a 200, or an
    entry that names no type or no ruleset id, has to fall through to the
    `.protected` floor rather than abort a gate whose failure also suppresses
    the outage report.
    """
    assert security_preflight.ruleset_ids({"message": "Not Found"}, "update") == []
    assert security_preflight.ruleset_ids(
        [{"ruleset_id": 1}, {"type": "update"}, "not a rule", _update_rule(4)],
        "update",
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
    assert "::error::No restrict-updates ruleset covers 'main', so" in (
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


def test_yolo_requires_pull_request_only_bypass_and_control_plane_review(
    monkeypatch: pytest.MonkeyPatch,
    fake_gh: FakeGh,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TEND_MERGE", "yolo")
    _codeowners(fake_gh)
    _repo(
        fake_gh,
        rules=[*_lifecycle_rules(1), {"type": "pull_request", "ruleset_id": 2}],
    )
    _bypass(fake_gh, 1, "pull_requests_only")
    fake_gh.respond(
        "api",
        f"repos/{REPO}/rulesets/2",
        with_={
            "current_user_can_bypass": "never",
            "rules": [
                {
                    "type": "pull_request",
                    "parameters": {
                        "require_code_owner_review": True,
                        "dismiss_stale_reviews_on_push": True,
                    },
                }
            ],
        },
    )

    assert security_preflight.main() == 0
    assert "direct pushes" in capsys.readouterr().out


def test_yolo_requires_creation_and_deletion_protection(
    monkeypatch: pytest.MonkeyPatch,
    fake_gh: FakeGh,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TEND_MERGE", "yolo")
    _codeowners(fake_gh)
    _repo(fake_gh, rules=[_update_rule(1)])
    _bypass(fake_gh, 1, "pull_requests_only")

    assert security_preflight.main() == 1
    assert "creation and deletion" in capsys.readouterr().out


@pytest.mark.parametrize("bypass", ["always", "never"])
def test_yolo_rejects_the_wrong_update_bypass(
    bypass: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_gh: FakeGh,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TEND_MERGE", "yolo")
    _codeowners(fake_gh)
    _repo(fake_gh, rules=[_update_rule(1)])
    _bypass(fake_gh, 1, bypass)

    assert security_preflight.main() == 1
    assert f"GitHub reported {bypass}" in capsys.readouterr().out


def test_yolo_rejects_a_bypassable_control_plane_rule(
    monkeypatch: pytest.MonkeyPatch,
    fake_gh: FakeGh,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TEND_MERGE", "yolo")
    _codeowners(fake_gh)
    _repo(
        fake_gh,
        rules=[*_lifecycle_rules(1), {"type": "pull_request", "ruleset_id": 2}],
    )
    _bypass(fake_gh, 1, "pull_requests_only")
    fake_gh.respond(
        "api",
        f"repos/{REPO}/rulesets/2",
        with_={
            "current_user_can_bypass": "pull_requests_only",
            "rules": [
                {
                    "type": "pull_request",
                    "parameters": {
                        "require_code_owner_review": True,
                        "dismiss_stale_reviews_on_push": True,
                    },
                }
            ],
        },
    )

    assert security_preflight.main() == 1
    assert "fresh CODEOWNER approval" in capsys.readouterr().out


def test_yolo_rejects_the_bot_as_control_plane_owner(
    monkeypatch: pytest.MonkeyPatch,
    fake_gh: FakeGh,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TEND_MERGE", "yolo")
    monkeypatch.setenv("TEND_CONTROL_PLANE_OWNER", "@tend-bot")
    _codeowners(fake_gh)
    _repo(fake_gh, rules=[_update_rule(1)])
    _bypass(fake_gh, 1, "pull_requests_only")

    assert security_preflight.main() == 1
    assert "not the Tend bot account" in capsys.readouterr().out


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


def test_a_readable_bypass_is_not_hidden_by_an_unreadable_ruleset(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(fake_gh, rules=[_update_rule(1), _update_rule(2)], protected=True)
    _bypass(fake_gh, 1, "always")
    fake_gh.respond("api", f"repos/{REPO}/rulesets/2", with_=1)

    assert security_preflight.main() == 1
    assert "can bypass every restrict-updates ruleset" in capsys.readouterr().out


def test_a_ruleset_body_without_the_bypass_field_falls_back_to_the_floor(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """An error object under a 200 carries no bypass verdict, so it is unread
    like a failed call rather than an answer that the bot can bypass."""
    _repo(fake_gh, rules=[_update_rule(1)], protected=True)
    fake_gh.respond("api", f"repos/{REPO}/rulesets/1", with_={"message": "502"})

    assert security_preflight.main() == 0
    assert "default branch 'main' is protected" in capsys.readouterr().out


def test_a_null_bypass_verdict_counts_as_bypassable(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A present field is GitHub's answer, and any answer but "never" fails."""
    _repo(fake_gh, rules=[_update_rule(1)])
    _bypass(fake_gh, 1, None)

    assert security_preflight.main() == 1
    assert "::error::The bot can bypass every" in capsys.readouterr().out


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
        "::error::No restrict-updates ruleset covers 'main', so"
        in capsys.readouterr().out
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
