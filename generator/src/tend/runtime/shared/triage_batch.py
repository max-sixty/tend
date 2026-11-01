"""Transport selected issue IDs through triage dispatch and matrix expansion.

Selection judgment belongs to notifications/recovery. Dispatch preserves its
order while deduplicating and splitting at GitHub's run-name and matrix limits.
The selector rejects inputs whose run name would truncate, then rechecks live
issues after the batch acquires its workflow lane. Open issues remain the
recovery backlog after a cancelled or failed run.
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


def compact(numbers: list[int]) -> str:
    return json.dumps(numbers, separators=(",", ":"))


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
    env = _common.require_env(
        "GITHUB_REPOSITORY", "BOOKKEEPING_LABELS", "ISSUES", "GITHUB_OUTPUT"
    )
    raw = env["ISSUES"]
    if len(RUN_NAME_PREFIX + raw) > RUN_NAME_LIMIT:
        raise ValueError(
            "triage run name exceeds GitHub's 512-character limit; "
            "use tend runtime triage-batch dispatch to split the selection"
        )
    numbers = issue_numbers(raw)
    if len(numbers) > MATRIX_LIMIT:
        raise ValueError("a triage batch cannot exceed GitHub's 256-job matrix limit")
    bookkeeping = set(json.loads(env["BOOKKEEPING_LABELS"]))
    selected = []
    for number in numbers:
        issue = _common.gh_json(
            "api", f"repos/{env['GITHUB_REPOSITORY']}/issues/{number}"
        )
        if (
            issue["state"] == "open"
            and "pull_request" not in issue
            and not bookkeeping.intersection(label["name"] for label in issue["labels"])
        ):
            selected.append(number)
    _common.set_output("issues", compact(selected))
    return 0
