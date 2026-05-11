"""Tests for the website data fetcher."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make the sibling script importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_website_data as fwd  # noqa: E402


@pytest.fixture(autouse=True)
def reset_gh_get():
    """Restore the real HTTP function after each test."""
    original = fwd._gh_get_fn
    yield
    fwd.set_gh_get(original)


def stub_responses(responses: dict[str, Any]) -> None:
    """Install a stub _gh_get that returns from a path-keyed dict."""

    def fake(path: str, token: str | None) -> Any:
        if path not in responses:
            raise AssertionError(f"unexpected GET {path}")
        return responses[path]

    fwd.set_gh_get(fake)


# ---------------------------------------------------------------------------
# load_consumers


def test_load_consumers_valid(tmp_path: Path) -> None:
    p = tmp_path / "consumers.json"
    p.write_text(json.dumps([{"repo": "max-sixty/tend", "bot_name": "tend-agent"}]))
    assert fwd.load_consumers(p) == [
        {"repo": "max-sixty/tend", "bot_name": "tend-agent"}
    ]


def test_load_consumers_missing_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        fwd.load_consumers(tmp_path / "nope.json")
    assert "not found" in str(exc.value)


def test_load_consumers_malformed(tmp_path: Path) -> None:
    p = tmp_path / "consumers.json"
    p.write_text(json.dumps([{"repo": "x"}]))
    with pytest.raises(SystemExit) as exc:
        fwd.load_consumers(p)
    assert "{repo, bot_name}" in str(exc.value)


# ---------------------------------------------------------------------------
# fetch_activity


def _item(html_url: str, repo: str, title: str, updated_at: str) -> dict[str, Any]:
    return {
        "html_url": html_url,
        "repository_url": f"https://api.github.com/repos/{repo}",
        "title": title,
        "updated_at": updated_at,
    }


def test_fetch_activity_dedupes_across_bots_and_kinds() -> None:
    pr_url = "https://github.com/o/r/pull/1"
    stub_responses(
        {
            # ci-fix queries
            "/search/issues?q=author%3Abot-a+is%3Apr&per_page=10&sort=updated&order=desc": {
                "items": [_item(pr_url, "o/r", "ci fix", "2026-05-09T10:00:00Z")],
            },
            "/search/issues?q=author%3Abot-b+is%3Apr&per_page=10&sort=updated&order=desc": {
                "items": [],
            },
            # review queries
            "/search/issues?q=commenter%3Abot-a+is%3Apr+-author%3Abot-a&per_page=10&sort=updated&order=desc": {
                "items": [
                    _item(
                        "https://github.com/o/r/pull/2",
                        "o/r",
                        "review me",
                        "2026-05-10T12:00:00Z",
                    ),
                    # Same PR as ci-fix; must dedupe — first kind wins.
                    _item(pr_url, "o/r", "ci fix", "2026-05-09T10:00:00Z"),
                ],
            },
            "/search/issues?q=commenter%3Abot-b+is%3Apr+-author%3Abot-b&per_page=10&sort=updated&order=desc": {
                "items": [],
            },
            # triage queries
            "/search/issues?q=commenter%3Abot-a+is%3Aissue&per_page=10&sort=updated&order=desc": {
                "items": [
                    _item(
                        "https://github.com/o/r/issues/3",
                        "o/r",
                        "bug",
                        "2026-05-10T08:00:00Z",
                    )
                ],
            },
            "/search/issues?q=commenter%3Abot-b+is%3Aissue&per_page=10&sort=updated&order=desc": {
                "items": [],
            },
        }
    )
    out = fwd.fetch_activity(["bot-a", "bot-b"], token=None)
    urls = [e["url"] for e in out["events"]]
    assert urls.count(pr_url) == 1
    ats = [e["at"] for e in out["events"]]
    assert ats == sorted(ats, reverse=True)
    kinds = {e["url"]: e["kind"] for e in out["events"]}
    assert kinds[pr_url] == "ci-fix"
    assert kinds["https://github.com/o/r/pull/2"] == "review"
    assert kinds["https://github.com/o/r/issues/3"] == "triage"


# ---------------------------------------------------------------------------
# fetch_stats


def test_fetch_stats_sums_across_bots() -> None:
    def fake(path: str, token: str | None) -> dict[str, Any]:
        assert path.startswith("/search/issues?")
        # Each (stat × bot) returns 10 — the test expects the per-stat sum
        # to be 20 (two bots).
        return {"total_count": 10}

    fwd.set_gh_get(fake)
    out = fwd.fetch_stats(["bot-a", "bot-b"], token=None)
    for key in (
        "reviews_total",
        "reviews_this_week",
        "ci_fixes_total",
        "ci_fixes_this_week",
        "triage_comments_total",
    ):
        assert out[key] == 20, key
    assert "generated_at" in out


# ---------------------------------------------------------------------------
# _gh_get retry behavior


def test_gh_get_retries_then_succeeds(monkeypatch) -> None:
    """A 403 followed by a 200 should be retried, not propagated."""
    calls: list[int] = []
    responses = [
        ("error", 403),
        ("ok", 200),
    ]

    class FakeResp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        kind, code = responses[len(calls)]
        calls.append(code)
        if kind == "error":
            import urllib.error

            raise urllib.error.HTTPError(req.full_url, code, "err", {}, None)
        return FakeResp(b'{"total_count": 7}')

    monkeypatch.setattr(fwd.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fwd.time, "sleep", lambda _s: None)

    out = fwd._gh_get("/search/issues?q=test", token=None)
    assert out == {"total_count": 7}
    assert calls == [403, 200]


def test_gh_get_404_exits_without_retry(monkeypatch) -> None:
    """A 404 is a config problem — fail fast, don't retry."""
    import io
    import urllib.error

    calls: list[int] = []

    def fake_urlopen(req, timeout):
        calls.append(404)
        raise urllib.error.HTTPError(
            req.full_url, 404, "not found", {}, io.BytesIO(b"not found")
        )

    monkeypatch.setattr(fwd.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fwd.time, "sleep", lambda _s: None)

    with pytest.raises(SystemExit):
        fwd._gh_get("/repos/x/y", token=None)
    assert calls == [404]


def test_gh_get_retries_network_errors(monkeypatch) -> None:
    """URLError (network blips) should retry, not propagate."""
    calls: list[str] = []

    class FakeResp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        if not calls:
            calls.append("err")
            import urllib.error

            raise urllib.error.URLError("connection reset")
        calls.append("ok")
        return FakeResp(b'{"items": []}')

    monkeypatch.setattr(fwd.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fwd.time, "sleep", lambda _s: None)

    out = fwd._gh_get("/search/issues?q=test", token=None)
    assert out == {"items": []}
    assert calls == ["err", "ok"]


# ---------------------------------------------------------------------------
# write_if_changed


def test_write_if_changed_creates_new_file(tmp_path: Path) -> None:
    out = tmp_path / "data" / "activity.json"
    payload = {"generated_at": "2026-05-10T00:00:00Z", "events": []}
    assert fwd.write_if_changed(out, payload) is True
    assert out.exists()


def test_write_if_changed_skips_when_structural_match(tmp_path: Path) -> None:
    out = tmp_path / "stats.json"
    out.write_text(
        json.dumps({"generated_at": "2026-05-10T00:00:00Z", "reviews_total": 5})
    )
    payload = {"generated_at": "2026-05-10T01:00:00Z", "reviews_total": 5}
    assert fwd.write_if_changed(out, payload) is False


def test_write_if_changed_writes_when_content_differs(tmp_path: Path) -> None:
    out = tmp_path / "stats.json"
    out.write_text(
        json.dumps({"generated_at": "2026-05-10T00:00:00Z", "reviews_total": 5})
    )
    payload = {"generated_at": "2026-05-10T01:00:00Z", "reviews_total": 6}
    assert fwd.write_if_changed(out, payload) is True
    reread = json.loads(out.read_text())
    assert reread["reviews_total"] == 6
