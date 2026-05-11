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
# fetch_activity


def _item(html_url: str, repo: str, title: str, updated_at: str) -> dict[str, Any]:
    return {
        "html_url": html_url,
        "repository_url": f"https://api.github.com/repos/{repo}",
        "title": title,
        "updated_at": updated_at,
    }


def test_fetch_activity_dedupes_and_sorts() -> None:
    pr_url = "https://github.com/o/r/pull/1"
    stub_responses(
        {
            "/search/issues?q=author%3Atend-agent+is%3Apr&per_page=10&sort=updated&order=desc": {
                "items": [_item(pr_url, "o/r", "ci fix", "2026-05-09T10:00:00Z")],
            },
            "/search/issues?q=commenter%3Atend-agent+is%3Apr+-author%3Atend-agent&per_page=10&sort=updated&order=desc": {
                "items": [
                    _item(
                        "https://github.com/o/r/pull/2",
                        "o/r",
                        "review me",
                        "2026-05-10T12:00:00Z",
                    ),
                    # Same PR as ci-fix query — must dedupe.
                    _item(pr_url, "o/r", "ci fix", "2026-05-09T10:00:00Z"),
                ],
            },
            "/search/issues?q=commenter%3Atend-agent+is%3Aissue&per_page=10&sort=updated&order=desc": {
                "items": [
                    _item(
                        "https://github.com/o/r/issues/3",
                        "o/r",
                        "bug report",
                        "2026-05-10T08:00:00Z",
                    )
                ],
            },
        }
    )
    out = fwd.fetch_activity("tend-agent", token=None)
    urls = [e["url"] for e in out["events"]]
    assert urls.count(pr_url) == 1
    ats = [e["at"] for e in out["events"]]
    assert ats == sorted(ats, reverse=True)
    kinds = {e["url"]: e["kind"] for e in out["events"]}
    assert kinds[pr_url] == "ci-fix"
    assert kinds["https://github.com/o/r/pull/2"] == "review"
    assert kinds["https://github.com/o/r/issues/3"] == "triage"


def test_fetch_activity_truncates_to_limit() -> None:
    stub_responses(
        {
            "/search/issues?q=author%3Atend-agent+is%3Apr&per_page=2&sort=updated&order=desc": {
                "items": [
                    _item(
                        f"https://github.com/o/r/pull/{i}",
                        "o/r",
                        f"t{i}",
                        f"2026-05-0{i}T00:00:00Z",
                    )
                    for i in (1, 2)
                ],
            },
            "/search/issues?q=commenter%3Atend-agent+is%3Apr+-author%3Atend-agent&per_page=2&sort=updated&order=desc": {
                "items": [
                    _item(
                        f"https://github.com/o/r/pull/{i}",
                        "o/r",
                        f"t{i}",
                        f"2026-05-0{i}T00:00:00Z",
                    )
                    for i in (3, 4)
                ],
            },
            "/search/issues?q=commenter%3Atend-agent+is%3Aissue&per_page=2&sort=updated&order=desc": {
                "items": [
                    _item(
                        f"https://github.com/o/r/issues/{i}",
                        "o/r",
                        f"t{i}",
                        f"2026-05-0{i}T00:00:00Z",
                    )
                    for i in (5, 6)
                ],
            },
        }
    )
    out = fwd.fetch_activity("tend-agent", token=None, limit=2)
    assert len(out["events"]) == 2


# ---------------------------------------------------------------------------
# fetch_stats


def test_fetch_stats_extracts_total_count() -> None:
    # Match the URL prefix per stat — exact querystrings depend on the date,
    # so the stub uses a Callable.
    counts = {
        "reviews_total": 412,
        "reviews_this_week": 18,
        "ci_fixes_total": 89,
        "ci_fixes_this_week": 3,
        "triage_comments_total": 67,
    }

    def fake(path: str, token: str | None) -> dict[str, Any]:
        assert path.startswith("/search/issues?")
        q = urllib_qs(path)["q"][0]
        if q.startswith("author:tend-agent is:pr") and "updated:" in q:
            return {"total_count": counts["ci_fixes_this_week"]}
        if q.startswith("author:tend-agent is:pr"):
            return {"total_count": counts["ci_fixes_total"]}
        if "is:pr" in q and "-author:tend-agent" in q and "updated:" in q:
            return {"total_count": counts["reviews_this_week"]}
        if "is:pr" in q and "-author:tend-agent" in q:
            return {"total_count": counts["reviews_total"]}
        if "is:issue" in q:
            return {"total_count": counts["triage_comments_total"]}
        raise AssertionError(f"unexpected query: {q}")

    fwd.set_gh_get(fake)
    out = fwd.fetch_stats("tend-agent", token=None)
    for k, v in counts.items():
        assert out[k] == v, k
    assert "generated_at" in out


def urllib_qs(path: str) -> dict[str, list[str]]:
    """Helper: parse the query string from a /search/issues path."""
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(path).query)


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
