from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import report_failure
from _fakes import FakeGh

BOT = "tend-agent"
NEW_ISSUE = 42
RUN_LINK = "[workflow run](https://github.com/owner/repo/actions/runs/12345)"
ROW = f"| when | {RUN_LINK} | #851 |"
COMMENTS = f"repos/owner/repo/issues/{NEW_ISSUE}/comments?per_page=100"
ACTION_REPOSITORY = "max-sixty/tend"
RELEASES = ("api", f"repos/{ACTION_REPOSITORY}/releases/latest")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reporter jitters before its check-then-act and settles before the
    reconcile; real sleeps add up."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


@pytest.fixture
def gh(fake_gh: FakeGh, actions_env: Path) -> FakeGh:
    """A `gh` wired for the create path: no tracker open, no racing sibling."""
    fake_gh.respond("api", "user", with_={"login": BOT, "id": 4242})
    fake_gh.respond("issue", "list", with_=[])
    fake_gh.respond(
        "issue", "create", with_=f"https://github.com/owner/repo/issues/{NEW_ISSUE}\n"
    )
    fake_gh.respond("issue", "view", with_={"body": "", "comments": []})
    for verb in ("comment", "close"):
        fake_gh.respond("issue", verb, with_="")
    fake_gh.respond("label", "create", with_="")
    fake_gh.respond("api", "--paginate", "--slurp", COMMENTS, with_=[[]])
    fake_gh.respond("api", "-X", "DELETE", with_="")
    # Ten as a literal, not off `_issue.PROBE_WINDOW`: a widened probe window
    # then reaches a number nothing seeded, which `FakeGh` refuses.
    for number in range(NEW_ISSUE - 1, NEW_ISSUE - 11, -1):
        fake_gh.respond("api", f"repos/owner/repo/issues/{number}", with_=404)
    return fake_gh


def _open_tracker(gh: FakeGh, number: int = NEW_ISSUE) -> None:
    """A tracker already open, so the reporter takes the append path."""
    gh.respond(
        "issue", "list", with_=[{"number": number, "title": report_failure.TITLE}]
    )
    gh.respond(
        "api",
        "--paginate",
        "--slurp",
        f"repos/owner/repo/issues/{number}/comments?per_page=100",
        with_=[[]],
    )


def _seen_by_the_guard(gh: FakeGh, *comments: str, body: str = "") -> None:
    """What `gh issue view --json body,comments` returns for the tracker."""
    gh.respond(
        "issue",
        "view",
        with_={"body": body, "comments": [{"body": c} for c in comments]},
    )


def _probe(gh: FakeGh, number: int, **overrides: Any) -> None:
    gh.respond(
        "api",
        f"repos/owner/repo/issues/{number}",
        with_={
            "number": number,
            "state": "open",
            "title": report_failure.TITLE,
            "user": {"login": BOT},
            "labels": [{"name": report_failure.LABEL}],
            **overrides,
        },
    )


def _comment(number: int, body: str, at: str) -> dict[str, Any]:
    return {"id": number, "created_at": at, "body": body}


def _posted(gh: FakeGh) -> str:
    """Every comment body the reporter handed `gh` on stdin, concatenated."""
    return "\n".join(
        stdin or ""
        for call, stdin in zip(gh.calls, gh.stdins, strict=True)
        if call[:2] == ("issue", "comment")
    )


def _deleted(gh: FakeGh) -> list[str]:
    """Comment ids the reconcile deleted."""
    return [call[-1].rsplit("/", 1)[-1] for call in gh.called("api", "-X", "DELETE")]


def _created_body(gh: FakeGh) -> str:
    """The body the reporter seeded a new tracker with."""
    for call, stdin in zip(gh.calls, gh.stdins, strict=True):
        if call[:2] == ("issue", "create"):
            return stdin or ""
    raise AssertionError(f"nothing was created: {gh.calls}")


def _pinned(
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    latest: Any = "0.2.10",
) -> None:
    """The action running at *ref*, with *latest* as the newest release.

    An ``int`` for *latest* makes the release read fail with that exit code.
    """
    monkeypatch.setenv("TEND_ACTION_REF", ref)
    monkeypatch.setenv("TEND_ACTION_REPOSITORY", ACTION_REPOSITORY)
    gh.respond(
        *RELEASES, with_=latest if isinstance(latest, int) else {"tag_name": latest}
    )


def test_files_when_nothing_is_open(gh: FakeGh) -> None:
    """No open tracker and no racing sibling: file one and keep it."""
    assert report_failure.main() == 0
    assert gh.called("issue", "create"), gh.calls
    assert not gh.called("issue", "close"), (
        f"closed the tracker it had just filed: {gh.calls}"
    )
    assert RUN_LINK in _created_body(gh), "the seed body carries this run's row"


def test_appends_to_the_open_tracker(gh: FakeGh) -> None:
    """An open tracker takes the row as a comment rather than a second issue.

    Only an *open* one: a closed tracker means the sweep diagnosed every row
    and checked the live repository, so the next incident gets a fresh issue
    rather than being folded into a stale record. That is the deliberate
    difference from the rate-limit issue, whose lookup is over every state, so
    the scope is asserted on the call rather than left to the fixture.
    """
    _open_tracker(gh, 8)

    assert report_failure.main() == 0
    assert gh.called("issue", "comment", "8"), gh.calls
    assert not gh.called("issue", "create"), (
        f"filed a second tracker while one was open: {gh.calls}"
    )
    for call in gh.called("issue", "list"):
        assert call[call.index("--state") + 1] == "open", (
            f"a closed tracker would swallow the next incident: {call}"
        )


def test_files_nothing_when_the_issue_list_cannot_be_read(
    gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed read is not "nothing is open", whichever shape it takes.

    A blip can answer under a 200 with an HTML error page, so `gh` exits zero
    and the parse fails; both readings have to skip rather than file.

    Two open trackers is the state that breaks the review sweep: later rows
    scatter across both and neither carries the complete set. The reconcile's
    downward probe does not reach an older tracker, so the duplicate persists.
    Skipping costs this one row, and the next failure records normally.
    """
    gh.respond("issue", "list", with_="<html>502</html>")

    assert report_failure.main() == 0
    assert not gh.called("issue", "create"), gh.calls
    assert "::warning::" in capsys.readouterr().out


def test_survives_a_failed_append_to_the_open_tracker(
    gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 5xx on the append must not abort the step.

    This is the common write path — once a tracker is open, every later
    failure in the same incident appends through it. Aborting costs a second
    red step on an already-failing run and drops the row silently, so the
    tracker under-reports the outage and a run stranded by it reads as one
    that never happened.

    The paired `..._propagates_a_failed_create` below asserts the opposite for
    the other branch, and the asymmetry is the point: an append has a tracker
    already carrying the incident, a create has nothing to fall back to.
    """
    _open_tracker(gh, 8)
    gh.respond("issue", "comment", with_=1)

    assert report_failure.main() == 0
    assert "::warning::" in capsys.readouterr().out
    assert not gh.called("issue", "create"), (
        f"filed a second tracker after the append failed: {gh.calls}"
    )
    assert not gh.called("api", "-X", "DELETE"), (
        f"reconciled after a row that never landed: {gh.calls}"
    )


def test_propagates_a_failed_create(
    gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A create that fails must redden the step, not report a phantom issue.

    With no tracker open there is no other record of the outage, so this is
    the one write here that has to fail loudly.
    """
    gh.respond("issue", "create", with_=1)

    assert report_failure.main() != 0
    assert "::error::" in capsys.readouterr().out


def test_carries_its_row_onto_the_racing_sibling(gh: FakeGh) -> None:
    """Standing down must not strand the failure it recorded.

    The row lives in the body of the issue this leg filed, so closing that
    issue takes the row with it unless it is carried onto the survivor first.

    Two siblings rather than one, because with a single match "lowest" and
    "nearest" are the same answer and the choice between them goes untested.
    Convergence rests on lowest: a third leg filing #43 sees both #41 and #38,
    and only if every leg keeps descending past the first hit do they agree on
    one keeper instead of scattering rows across two.
    """
    _probe(gh, 41)
    _probe(gh, 38)
    # A sibling from another workflow: its seed row cites a different run.
    _seen_by_the_guard(gh, body="run 999 row")

    assert report_failure.main() == 0
    assert gh.called("issue", "comment", "38"), (
        f"carried the row onto the nearest sibling rather than the lowest: {gh.calls}"
    )
    assert not gh.called("issue", "comment", "41"), (
        f"stopped at the first hit instead of descending to the lowest: {gh.calls}"
    )
    assert gh.called("issue", "close", str(NEW_ISSUE)), gh.calls
    assert any("Duplicate of #38" in arg for call in gh.calls for arg in call), gh.calls
    assert RUN_LINK in _posted(gh)


def test_does_not_repeat_a_row_the_keeper_already_has(gh: FakeGh) -> None:
    """Matrix legs share one run id, so the keeper's seed row is already ours."""
    _probe(gh, 41)
    _seen_by_the_guard(gh, body=ROW)

    assert report_failure.main() == 0
    assert gh.called("issue", "close", str(NEW_ISSUE)), gh.calls
    assert not gh.called("issue", "comment"), (
        f"repeated a row the keeper already carried: {gh.calls}"
    )


def test_does_not_adopt_a_foreign_issue(gh: FakeGh) -> None:
    """The bot holds `issues: write`, so the label alone nominates nothing."""
    _probe(gh, 41, user={"login": "someone"})
    _probe(gh, 40, title="A maintainer's issue")
    _probe(gh, 39, labels=[{"name": "unrelated-label"}])
    _probe(gh, 38, state="closed")

    assert report_failure.main() == 0
    assert not gh.called("issue", "close"), (
        f"stood down to an issue the reporter never filed: {gh.calls}"
    )


@pytest.mark.parametrize(
    ("body", "comments"),
    [
        pytest.param("", (ROW,), id="in-a-comment"),
        pytest.param(ROW, (), id="in-the-issue-body"),
    ],
)
def test_skips_a_run_already_recorded(
    gh: FakeGh, body: str, comments: tuple[str, ...]
) -> None:
    """A leg whose sibling already recorded this run posts nothing.

    This is the guard that collapses the flood: a matrix workflow calls this
    once per leg, every leg sharing one GITHUB_RUN_ID, so without it a 5-leg
    matrix leaves 5 comments all citing the same run. The body case is the
    first run of an outage: one leg seeds the issue with its row, and the
    siblings that follow have no comment to match — only the body.
    """
    _open_tracker(gh)
    _seen_by_the_guard(gh, *comments, body=body)

    assert report_failure.main() == 0
    assert not _posted(gh), (
        f"appended a second row for a run already recorded: {gh.calls}"
    )


def test_appends_a_row_for_an_unrecorded_run(gh: FakeGh) -> None:
    """The happy path: a run the tracker has not seen still gets its row."""
    _open_tracker(gh)
    _seen_by_the_guard(gh, "some other run's row")

    assert report_failure.main() == 0
    assert RUN_LINK in _posted(gh)


def test_reconciles_a_racing_leg(gh: FakeGh) -> None:
    """Two legs that both read the tracker before either posted converge to one row.

    The guard is a check-then-act, so jittered legs can both miss. Reading
    every page is what makes this work on the issues that need it: comments
    come back oldest-first, so on a flooded tracker the rows just posted are
    not on the first one.
    """
    _open_tracker(gh)
    _seen_by_the_guard(gh, "nothing recorded yet")
    gh.respond(
        "api",
        "--paginate",
        "--slurp",
        COMMENTS,
        with_=[
            [_comment(1, ROW, "2026-01-02T11:59:00Z")],
            [_comment(2, ROW, "2026-01-02T12:00:00Z")],
        ],
    )

    assert report_failure.main() == 0
    assert _deleted(gh) == ["2"], (
        f"expected the later of the two rows deleted, got {_deleted(gh)}"
    )
    assert gh.called("api", "--paginate", "--slurp", COMMENTS), (
        f"the reconcile did not ask for every page of comments: {gh.calls}"
    )


def test_survives_a_failed_reconcile_read(
    gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 5xx on the reconcile's read must not redden a step whose row landed.

    The reconcile is best-effort cleanup and runs after the write, so failing
    loudly would redden the `Report failure` step with no annotation naming
    why, on precisely the job someone is about to diagnose. Duplicate rows on
    the tracker are the better failure.
    """
    _open_tracker(gh)
    _seen_by_the_guard(gh, "nothing recorded yet")
    gh.respond("api", "--paginate", "--slurp", COMMENTS, with_="<html>502</html>")

    assert report_failure.main() == 0
    assert "::warning::" in capsys.readouterr().out
    assert RUN_LINK in _posted(gh), "lost the row the reconcile was cleaning up after"


def test_duplicate_rows_keeps_the_earliest_generated_row_alone() -> None:
    """The reconcile deletes, so its predicate is the whole protection.

    Selecting on the bare run URL would make a person linking the run in
    discussion — the normal way an outage gets diagnosed — a duplicate to be
    removed. Ordering is by `created_at` rather than id, so every racing leg
    computes the same keeper from the same list.
    """
    human = _comment(
        1,
        "https://github.com/owner/repo/actions/runs/12345 is the one that failed",
        "2026-01-02T11:00:00Z",
    )
    assert report_failure.duplicate_rows([human], RUN_LINK) == []

    comments = [
        _comment(9, ROW, "2026-01-02T12:00:00Z"),
        human,
        _comment(4, "an unrelated row", "2026-01-02T11:30:00Z"),
        _comment(7, ROW, "2026-01-02T11:59:00Z"),
    ]
    assert report_failure.duplicate_rows(comments, RUN_LINK) == [9]


def test_names_a_stale_pin_as_the_remedy(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin behind the newest release is the one cause the reader can clear.

    The blank line before the note is asserted with it: a prose line directly
    under the last table row renders as another row of the table.
    """
    _pinned(gh, monkeypatch, "0.2.9")

    assert report_failure.main() == 0
    body = _created_body(gh)
    assert report_failure.REMEDY_COMMAND in body, body
    assert "\n\nTend `0.2.10` is released" in body, body
    assert "`0.2.9`" in body, body


def test_says_nothing_when_the_pin_is_current(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the newest release a boot failure is something else, and telling the
    reader to regenerate would send them the wrong way."""
    _pinned(gh, monkeypatch, "0.2.10")

    assert report_failure.main() == 0
    assert report_failure.REMEDY_COMMAND not in _created_body(gh)


def test_reads_no_release_for_a_ref_that_is_not_one(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A branch, a SHA, or a local path cannot be ordered against a release,
    so there is nothing to ask GitHub about."""
    monkeypatch.setenv("TEND_ACTION_REF", "main")
    monkeypatch.setenv("TEND_ACTION_REPOSITORY", ACTION_REPOSITORY)

    assert report_failure.main() == 0
    assert not gh.called(*RELEASES), gh.calls
    assert report_failure.REMEDY_COMMAND not in _created_body(gh)


def test_survives_a_failed_release_read(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is the record; the remedy is a hint on top of it."""
    _pinned(gh, monkeypatch, "0.2.9", latest=502)

    assert report_failure.main() == 0
    assert RUN_LINK in _created_body(gh)
    assert report_failure.REMEDY_COMMAND not in _created_body(gh)


def test_the_remedy_rides_the_first_row_after_the_release(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release that ends an outage normally lands while it is still
    running, so the note cannot be confined to the seed body — at file time
    there was no newer release to name."""
    _open_tracker(gh)
    _seen_by_the_guard(gh, "an earlier run's row", body="the seed body")
    _pinned(gh, monkeypatch, "0.2.9")

    assert report_failure.main() == 0
    assert report_failure.REMEDY_COMMAND in _posted(gh), _posted(gh)


def test_the_remedy_is_said_once_per_tracker(
    gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One incident appends a row per stranded run and the command is the same
    each time, so anything already naming it — an earlier row as much as a
    maintainer diagnosing in the thread — settles it, before the release read.
    """
    _open_tracker(gh)
    _seen_by_the_guard(gh, f"try `{report_failure.REMEDY_COMMAND}`")
    _pinned(gh, monkeypatch, "0.2.9")

    assert report_failure.main() == 0
    assert RUN_LINK in _posted(gh), "dropped the row along with the remedy"
    assert report_failure.REMEDY_COMMAND not in _posted(gh), _posted(gh)
    assert not gh.called(*RELEASES), gh.calls


@pytest.mark.parametrize("ref", ["main", "0.2", "0.2.9.1", "v0.2.9", "1.².3", ""])
def test_a_ref_that_is_not_a_release_is_unordered(ref: str) -> None:
    """Every non-release ref returns ``None`` rather than raising.

    ``1.².3`` is the one that needs the strict predicate: ``str.isdigit`` is
    true for it and ``int`` is not, so a laxer guard raises inside a step that
    is already red and loses the run's row.
    """
    assert report_failure._release(ref) is None
