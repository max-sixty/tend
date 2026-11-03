"""Carry selected issue IDs through triage dispatch, lanes and live checks.

Selection judgment belongs to notifications/recovery. Dispatch preserves its
order while deduplicating and splitting at GitHub's run-name and matrix limits.
The selector rejects inputs whose run name would truncate and routes each issue
to its lane, ``issue % LANES``, without reading GitHub. Each issue job checks
its live issue once it holds its lane, since the job may have waited hours for
it: a closed issue, a pull request or a bookkeeping issue ends the job before
setup and the agent run. Open issues remain the recovery backlog after a
cancelled or failed run.
"""

from __future__ import annotations

import json

from tend.runtime.shared import _common

RUN_NAME_PREFIX = "triage "
RUN_NAME_LIMIT = 512
MATRIX_LIMIT = 256


def issue_numbers(raw: str) -> list[int]:
    numbers = json.loads(raw)
    if not isinstance(numbers, list) or any(
        type(number) is not int or number < 1 for number in numbers
    ):
        raise ValueError("issues must be a JSON array of positive issue numbers")
    return list(dict.fromkeys(numbers))


def compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def dispatch_batches(raw: str) -> list[str]:
    batches = []
    batch: list[int] = []
    for number in issue_numbers(raw):
        if len(RUN_NAME_PREFIX + compact([number])) > RUN_NAME_LIMIT:
            raise ValueError("an issue number exceeds GitHub's run-name limit")
        candidate = [*batch, number]
        if (
            len(candidate) > MATRIX_LIMIT
            or len(RUN_NAME_PREFIX + compact(candidate)) > RUN_NAME_LIMIT
        ):
            batches.append(compact(batch))
            batch = [number]
        else:
            batch = candidate
    if batch:
        batches.append(compact(batch))
    return batches


def dispatch(raw: str) -> int:
    # Validate the whole selection before submitting any of it.
    batches = dispatch_batches(raw)
    repo = _common.require_env("GITHUB_REPOSITORY")["GITHUB_REPOSITORY"]
    for batch in batches:
        result = _common.gh(
            "workflow",
            "run",
            "tend-triage.yaml",
            "--repo",
            repo,
            "-f",
            f"issues={batch}",
        )
        print(result, end="", flush=True)
    return 0


def select() -> int:
    env = _common.require_env("ISSUES", "LANES", "GITHUB_OUTPUT")
    raw = env["ISSUES"]
    if len(RUN_NAME_PREFIX + raw) > RUN_NAME_LIMIT:
        raise ValueError(
            "triage run name exceeds GitHub's 512-character limit; "
            "use tend runtime triage-batch dispatch to split the selection"
        )
    numbers = issue_numbers(raw)
    if len(numbers) > MATRIX_LIMIT:
        raise ValueError("a triage batch cannot exceed GitHub's 256-job matrix limit")
    lanes = int(env["LANES"])
    legs = [{"issue": number, "lane": number % lanes} for number in numbers]
    _common.set_output("legs", compact(legs))
    return 0


def eligible() -> int:
    env = _common.require_env(
        "GITHUB_REPOSITORY", "BOOKKEEPING_LABELS", "TEND_ISSUE_NUMBER", "GITHUB_OUTPUT"
    )
    number = env["TEND_ISSUE_NUMBER"]
    issue = _common.gh_json("api", f"repos/{env['GITHUB_REPOSITORY']}/issues/{number}")
    bookkeeping = set(json.loads(env["BOOKKEEPING_LABELS"]))
    ok = (
        issue["state"] == "open"
        and "pull_request" not in issue
        and not bookkeeping.intersection(label["name"] for label in issue["labels"])
    )
    if not ok:
        print(f"#{number} is closed, a pull request or bookkeeping; no session")
    _common.set_output("eligible", str(ok).lower())
    return 0
