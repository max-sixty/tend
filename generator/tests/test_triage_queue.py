"""Exercise durable triage labels through the script's HTTP boundary."""

from __future__ import annotations

import importlib.resources
import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(importlib.resources.files("tend") / "templates"))
import triage_queue as queue

REPO = "owner/repo"
ISSUE = f"repos/{REPO}/issues/7"
LABEL = f"repos/{REPO}/labels/{queue.LABEL}"
EVENTS = f"{ISSUE}/events?per_page=100&page=1"


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """HTTP responses stand in for GitHub; exercise real request serialization."""
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.example")
    monkeypatch.setenv("ISSUE_NUMBER", "7")
    monkeypatch.setenv("QUEUE_ACTION", "enqueue")
    monkeypatch.setenv("QUEUE_GENERATION", "0")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    replies = []

    def respond(request, *, timeout):
        method, path, body, response = replies.pop(0)
        assert request.full_url == f"https://api.github.example/{path}"
        assert request.get_method() == method
        assert (json.loads(request.data) if request.data else None) == body
        assert request.get_header("Authorization") == "Bearer test-token"
        assert timeout == 30
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(queue, "urlopen", respond)
    yield replies
    assert replies == []


def issue(*, state="open", labels=(), **extra):
    return {"state": state, "labels": [{"name": name} for name in labels], **extra}


def error(status):
    return HTTPError("https://api.github.example", status, "API failure", {}, None)


def test_enqueue_creates_label_and_keeps_other_labels(github):
    github.extend(
        [
            ("GET", ISSUE, None, issue(labels=["bug"])),
            ("GET", EVENTS, None, []),
            ("GET", LABEL, None, error(404)),
            (
                "POST",
                f"repos/{REPO}/labels",
                {
                    "name": queue.LABEL,
                    "color": "ededed",
                    "description": "Tend issue triage has not completed successfully",
                },
                {},
            ),
            ("POST", f"{ISSUE}/labels", {"labels": [queue.LABEL]}, []),
        ]
    )
    assert queue.main() == 0
    assert (
        Path(queue.os.environ["GITHUB_OUTPUT"]).read_text()
        == "ready=true\ngeneration=0\n"
    )


def test_already_pending_issue_needs_no_writes(github):
    github.append(("GET", ISSUE, None, issue(labels=[queue.LABEL])))
    github.append(("GET", EVENTS, None, []))
    assert queue.main() == 0
    assert (
        Path(queue.os.environ["GITHUB_OUTPUT"]).read_text()
        == "ready=true\ngeneration=0\n"
    )


def test_closed_issue_is_not_admitted(github):
    github.append(("GET", ISSUE, None, issue(state="closed")))
    assert queue.main() == 0
    assert (
        Path(queue.os.environ["GITHUB_OUTPUT"]).read_text()
        == "ready=false\ngeneration=0\n"
    )


def test_pull_request_is_refused(github):
    github.append(("GET", ISSUE, None, issue(pull_request={})))
    with pytest.raises(ValueError, match="pull request"):
        queue.main()
    assert not Path(queue.os.environ["GITHUB_OUTPUT"]).exists()


@pytest.mark.parametrize("label_exists", [True, False])
def test_concurrent_label_creation_must_be_confirmed(github, label_exists):
    github.extend(
        [
            ("GET", ISSUE, None, issue()),
            ("GET", EVENTS, None, []),
            ("GET", LABEL, None, error(404)),
            (
                "POST",
                f"repos/{REPO}/labels",
                {
                    "name": queue.LABEL,
                    "color": "ededed",
                    "description": "Tend issue triage has not completed successfully",
                },
                error(422),
            ),
            ("GET", LABEL, None, {} if label_exists else error(404)),
        ]
    )
    if label_exists:
        github.append(("POST", f"{ISSUE}/labels", {"labels": [queue.LABEL]}, []))
        assert queue.main() == 0
    else:
        with pytest.raises(HTTPError):
            queue.main()
        assert not Path(queue.os.environ["GITHUB_OUTPUT"]).exists()


@pytest.mark.parametrize("response", [[], error(404)])
def test_complete_removes_only_pending_label_idempotently(
    github, monkeypatch, response
):
    monkeypatch.setenv("QUEUE_ACTION", "complete")
    monkeypatch.setenv("QUEUE_GENERATION", "23")
    reopened = [{"id": 23, "event": "reopened"}]
    github.append(("GET", EVENTS, None, reopened))
    github.append(("DELETE", f"{ISSUE}/labels/{queue.LABEL}", None, response))
    github.append(("GET", EVENTS, None, reopened))
    assert queue.main() == 0


@pytest.mark.parametrize("action", ["enqueue", "complete"])
@pytest.mark.parametrize("failure", [error(403), URLError("network unavailable")])
def test_auth_and_network_failures_surface(github, monkeypatch, action, failure):
    monkeypatch.setenv("QUEUE_ACTION", action)
    if action == "enqueue":
        github.extend(
            [
                ("GET", ISSUE, None, issue()),
                ("GET", EVENTS, None, []),
                ("GET", LABEL, None, failure),
            ]
        )
    else:
        github.append(("GET", EVENTS, None, []))
        github.append(("DELETE", f"{ISSUE}/labels/{queue.LABEL}", None, failure))
    with pytest.raises(type(failure)):
        queue.main()


@pytest.mark.parametrize("during_delete", [False, True])
def test_old_completion_preserves_new_reopen(github, monkeypatch, during_delete):
    monkeypatch.setenv("QUEUE_ACTION", "complete")
    reopened = [{"id": 23, "event": "reopened"}]
    github.append(("GET", EVENTS, None, [] if during_delete else reopened))
    if during_delete:
        github.extend(
            [
                ("DELETE", f"{ISSUE}/labels/{queue.LABEL}", None, []),
                ("GET", EVENTS, None, reopened),
                ("POST", f"{ISSUE}/labels", {"labels": [queue.LABEL]}, []),
            ]
        )
    assert queue.main() == 0


def test_admission_reads_every_event_page_for_latest_reopen(github):
    github.extend(
        [
            ("GET", ISSUE, None, issue(labels=[queue.LABEL])),
            (
                "GET",
                EVENTS,
                None,
                [{"id": 1, "event": "reopened"}]
                + [{"id": index, "event": "labeled"} for index in range(2, 101)],
            ),
            (
                "GET",
                f"{ISSUE}/events?per_page=100&page=2",
                None,
                [{"id": 102, "event": "reopened"}],
            ),
        ]
    )
    assert queue.main() == 0
    assert Path(queue.os.environ["GITHUB_OUTPUT"]).read_text() == (
        "ready=true\ngeneration=102\n"
    )


@pytest.mark.parametrize("completed", [True, False])
def test_queued_duplicate_admits_only_if_previous_worker_failed(
    github, monkeypatch, completed
):
    # Two events record the same lifecycle before either worker finishes.
    github.extend(
        [
            ("GET", ISSUE, None, issue()),
            ("GET", EVENTS, None, []),
            ("GET", LABEL, None, {}),
            ("POST", f"{ISSUE}/labels", {"labels": [queue.LABEL]}, []),
            ("GET", ISSUE, None, issue(labels=[queue.LABEL])),
            ("GET", EVENTS, None, []),
        ]
    )
    assert queue.main() == 0
    assert queue.main() == 0

    monkeypatch.setenv("QUEUE_ACTION", "admit")
    github.extend(
        [
            ("GET", ISSUE, None, issue(labels=[queue.LABEL])),
            ("GET", EVENTS, None, []),
        ]
    )
    assert queue.main() == 0
    if completed:
        monkeypatch.setenv("QUEUE_ACTION", "complete")
        github.extend(
            [
                ("GET", EVENTS, None, []),
                ("DELETE", f"{ISSUE}/labels/{queue.LABEL}", None, []),
                ("GET", EVENTS, None, []),
            ]
        )
        assert queue.main() == 0

    # The next worker cannot recreate work the first worker completed.
    monkeypatch.setenv("QUEUE_ACTION", "admit")
    github.append(
        ("GET", ISSUE, None, issue(labels=[] if completed else [queue.LABEL]))
    )
    if not completed:
        github.append(("GET", EVENTS, None, []))
    assert queue.main() == 0
    assert Path(queue.os.environ["GITHUB_OUTPUT"]).read_text().splitlines()[-2:] == [
        f"ready={str(not completed).lower()}",
        "generation=0",
    ]
