"""Move the job's checkout to the event's topology, inside the sandbox.

The checkout arrives on reviewed code; this selects the PR's merge or head ref
for a review, the head branch of the PR a mention names, and the base branch
otherwise, then restores the startup configuration a PR head must not choose.
It runs as the sandbox uid, so Git parses a contributor's packfile there, and
it holds no credential: the proxy authenticates every request. Writes land in
the view, so the runner's own checkout is unchanged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

STEP = "event-checkout"
OBJECT_ID = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\Z")
RESTORE_SENSITIVE_CONFIG = Path(__file__).with_name("restore-sensitive-config.sh")
BASH = "/usr/bin/bash"
MODES = frozenset({"base", "review", "mention"})


def log(message: str) -> None:
    print(f"[{STEP}] {message}", flush=True)


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is unset")
    return value


def git(
    *args: str,
    cwd: Path,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/git", *args],
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def api_json(path: str) -> dict[str, Any]:
    """One GitHub REST call, authenticated by the proxy rather than by us."""
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError(f"GitHub API returned a non-object for {path}")
    return value


def event_payload() -> dict[str, Any]:
    with Path(required("GITHUB_EVENT_PATH")).open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError("GITHUB_EVENT_PATH must contain a JSON object")
    return value


def event_number(payload: dict[str, Any]) -> int:
    raw = payload.get("number")
    if raw is None and isinstance(payload.get("issue"), dict):
        raw = payload["issue"].get("number")
    if raw is None and isinstance(payload.get("client_payload"), dict):
        raw = payload["client_payload"].get("pr")
    if isinstance(raw, str) and raw.isdecimal():
        raw = int(raw)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError("event does not identify a positive PR or issue number")
    return raw


def event_targets_pull_request(payload: dict[str, Any]) -> bool:
    """True where a mention event names a pull request rather than an issue.

    `issues` and `issue_comment` both carry an `issue`, which names a PR only
    through its `pull_request` link; a relayed review names one outright in
    `client_payload`.  Anything else has no PR head to fetch.
    """
    issue = payload.get("issue")
    if isinstance(issue, dict):
        return isinstance(issue.get("pull_request"), dict)
    return isinstance(payload.get("client_payload"), dict)


def checkout_base(workspace: Path, branch: str, sha: str) -> str:
    git("checkout", "-B", branch, sha, cwd=workspace)
    git("branch", "--set-upstream-to", f"origin/{branch}", branch, cwd=workspace)
    return f"base branch {branch} at {sha}"


def checkout_review(workspace: Path, number: int) -> str:
    for kind in ("merge", "head"):
        source = f"refs/pull/{number}/{kind}"
        result = git("fetch", "--no-tags", "origin", source, cwd=workspace, check=False)
        if result.returncode == 0:
            git("checkout", "--detach", "FETCH_HEAD", cwd=workspace)
            return f"PR #{number} {kind} ref"
    raise ValueError(f"neither merge nor head ref exists for PR #{number}")


def checkout_mention(
    workspace: Path,
    *,
    repository: str,
    number: int,
    base_branch: str,
    base_sha: str,
) -> tuple[str, str]:
    pr = api_json(f"/repos/{repository}/pulls/{number}")
    if pr.get("state") != "open":
        # The fallback selects the default branch, which is reviewed code — so
        # there is nothing to pin away, and pinning to the closed PR's base
        # would revert this tree's instruction files to whatever they were
        # when that PR opened.  Same answer as a mention on an issue thread.
        return checkout_base(workspace, base_branch, base_sha), ""
    head = pr.get("head")
    if not isinstance(head, dict) or not isinstance(head.get("repo"), dict):
        raise TypeError(f"PR #{number} has no fetchable head repository")
    branch = head.get("ref")
    head_sha = head.get("sha")
    clone_url = head["repo"].get("clone_url")
    if not isinstance(branch, str) or not isinstance(clone_url, str):
        raise TypeError(f"PR #{number} has an invalid head topology")
    head_sha = validated_object_id(head_sha, f"PR #{number} head")
    git("check-ref-format", "--branch", branch, cwd=workspace)
    git("remote", "remove", "tend-head", cwd=workspace, check=False, capture=True)
    git("remote", "add", "tend-head", clone_url, cwd=workspace)
    git(
        "fetch",
        "--no-tags",
        "tend-head",
        f"{head_sha}:refs/remotes/tend-head/{branch}",
        cwd=workspace,
    )
    git("checkout", "-B", branch, f"refs/remotes/tend-head/{branch}", cwd=workspace)
    git("branch", "--set-upstream-to", f"tend-head/{branch}", branch, cwd=workspace)
    return (
        f"open PR #{number} head {head['repo'].get('full_name')}:{branch} at {head_sha}",
        pull_base_sha(pr, number),
    )


def pull_base_sha(payload: dict[str, Any], number: int) -> str:
    pull = payload.get("pull_request", payload)
    base = pull.get("base") if isinstance(pull, dict) else None
    sha = base.get("sha") if isinstance(base, dict) else None
    return validated_object_id(sha, f"PR #{number} base")


def validated_object_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not OBJECT_ID.fullmatch(value):
        raise ValueError(f"{label} is not an exact Git object ID")
    return value


def ensure_commit(workspace: Path, sha: str) -> None:
    if not git(
        "cat-file", "-e", f"{sha}^{{commit}}", cwd=workspace, check=False
    ).returncode:
        return
    git("fetch", "--no-tags", "origin", sha, cwd=workspace)


def restore_sensitive_config(workspace: Path, base_sha: str) -> None:
    """Pin startup configuration the PR head must not choose for itself."""
    if not base_sha:
        return
    subprocess.run(
        [BASH, "--noprofile", "--norc", str(RESTORE_SENSITIVE_CONFIG)],
        cwd=workspace,
        env={**os.environ, "BASH_ENV": "", "TEND_CONFIG_BASE_SHA": base_sha},
        check=True,
    )


def main() -> int:
    workspace = Path(required("GITHUB_WORKSPACE")).resolve(strict=True)
    repository = required("GITHUB_REPOSITORY")
    mode = os.environ.get("TEND_CHECKOUT_MODE", "base")
    base_branch = os.environ.get("TEND_BASE_BRANCH") or required(
        "GITHUB_REPOSITORY_DEFAULT_BRANCH"
    )
    if mode not in MODES:
        raise ValueError(f"unsupported checkout mode: {mode}")

    # Read before anything moves HEAD: this is the reviewed commit the workflow
    # checked out, and the fallbacks below select it by name.
    base_sha = git("rev-parse", "HEAD", cwd=workspace, capture=True).stdout.strip()

    if mode == "base":
        selected = checkout_base(workspace, base_branch, base_sha)
        config_base_sha = ""
    else:
        payload = event_payload()
        number = event_number(payload)
        if mode == "review":
            selected = checkout_review(workspace, number)
            config_base_sha = pull_base_sha(payload, number)
        elif event_targets_pull_request(payload):
            selected, config_base_sha = checkout_mention(
                workspace,
                repository=repository,
                number=number,
                base_branch=base_branch,
                base_sha=base_sha,
            )
        else:
            # An issue thread: `number` is an issue number, which the PR
            # endpoint 404s on.  The default branch is reviewed code, so
            # it needs no pin either.
            selected = checkout_base(workspace, base_branch, base_sha)
            config_base_sha = ""
        if config_base_sha:
            ensure_commit(workspace, config_base_sha)

    restore_sensitive_config(workspace, config_base_sha)
    log(f"selected {selected} in {workspace}")
    return 0
