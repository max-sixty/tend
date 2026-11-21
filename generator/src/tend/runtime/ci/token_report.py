"""Report token use and spend across recent Tend workflow runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from tend.runtime.shared import _common

RUN_LIMIT = 1000
# The most a page of the repository's artifact listing holds.
ARTIFACT_PAGE = 100
# A run is priced when it *completed* in the window, matching how
# `list_recent_runs.py` censuses one — so the fetch has to reach back far
# enough to see a run that started before the window and finished inside it.
# Same value as that script's `CREATION_CUSHION`, and for the same reason.
CREATION_CUSHION = timedelta(hours=24)
# `gh workflow list` fetches 50 without one, and says nothing when it truncates.
# Same value as `list_recent_runs.py`'s, so the spend covers the census's fleet.
WORKFLOW_LIMIT = 200

REPORT_JQ = r"""
def sum(f): map(f) | add // 0;
def pick(f): map(f // empty) | first;

# Codex's `input_tokens` includes its cached input; Claude's excludes cache
# reads. Recast a Codex record in Claude's shape so every column means the
# same for both harnesses.
def claude_shape:
  if .harness == "codex"
  then .cache_read_input_tokens = (.cached_input_tokens // 0)
    | .input_tokens = (.input_tokens // 0) - .cache_read_input_tokens
  else .
  end;

def run_entry:
  {
    run_id: .[0].run_id,
    _order: .[0]._order,
    workflow: .[0].workflow,
    created_at: .[0].created_at,
    repo: pick(.repo),
    event: pick(.event),
    number: pick(.number),
    head_sha: pick(.head_sha),
    input_tokens: sum(.input_tokens),
    output_tokens: sum(.output_tokens),
    cache_creation_input_tokens: sum(.cache_creation_input_tokens),
    cache_read_input_tokens: sum(.cache_read_input_tokens),
    turns: sum(.turns),
    cost_usd: (if all(.[]; .harness == "codex") then null else sum(.cost_usd) end),
    partial: (map(.partial // false) | any),
    unpriced: (map(.harness == "codex" or (.partial // false)) | any)
  }
  | .subject = (
      if .number then "#\(.number)"
      elif .head_sha then .head_sha
      else "?"
      end
    );

def fmt:
  if . >= 1000000 then "\(. / 100000 | floor | . / 10)M"
  elif . >= 1000 then "\(. / 100 | floor | . / 10)K"
  else "\(.)"
  end;

def usd:
  ((. + 1e-12) * 100 | round / 100)
  | tostring
  | if test("\\.")
    then split(".") | "\(.[0]).\((.[1] + "00")[:2])"
    else . + ".00"
    end
  | "$" + .;

def floor_marker: if . then "+" else "" end;
def short: .[:12];
def pad($width): . + ((" " * ($width - length)) // "");

def table($rows):
  ($rows | transpose | map(map(length) | max)) as $widths
  | $rows
  | map(
      . as $row
      | [
          range(0; $row | length) as $index
          | $row[$index] | pad($widths[$index])
        ]
      | join("  ")
      | sub(" +$"; "")
    );

def rollup:
  {
    n: length,
    cost: sum(.cost_usd),
    partial: (map(.partial) | any),
    unpriced: (map(.unpriced) | any),
    i: sum(.input_tokens),
    o: sum(.output_tokens),
    cc: sum(.cache_creation_input_tokens),
    cr: sum(.cache_read_input_tokens)
  };

def cost_cell:
  if .unpriced and .cost == 0 then "n/a"
  else (.cost | usd) + (.unpriced | floor_marker)
  end;
def by_cost: sort_by(.cost, .cr) | reverse;
def subjects:
  group_by(.subject)
  | map({key: (.[0].subject | short), workflows: (map(.workflow) | unique | join(","))} + rollup);

def summary($since):
  .totals as $totals
  | .runs as $runs
  | (if ($runs | length) > 0 and $totals.unpriced_runs == ($runs | length) and $totals.cost_usd == 0
     then "n/a"
     else "\($totals.cost_usd | usd)\($totals.unpriced_runs > 0 | floor_marker)"
     end) as $cost_total
  | [
      "",
      "\($runs | length) runs since \($since)",
      "Total cost: \($cost_total)"
        + (if $totals.unpriced_runs > 0
           then " (\($totals.unpriced_runs) of \($runs | length) runs cost-unknown)"
           else ""
           end),
      "Tokens: \($totals.input_tokens | fmt) in, \($totals.output_tokens | fmt) out, \($totals.cache_creation_input_tokens | fmt) cache-create, \($totals.cache_read_input_tokens | fmt) cache-read",
      ""
    ]
  + table(
      [["WORKFLOW", "RUNS", "COST", "INPUT", "OUTPUT", "CACHE-CREATE", "CACHE-READ"]]
      + ($runs
         | group_by(.workflow)
         | map({key: .[0].workflow} + rollup)
         | by_cost
         | map([
             .key,
             (.n | tostring),
             cost_cell,
             (.i | fmt),
             (.o | fmt),
             (.cc | fmt),
             (.cr | fmt)
           ]))
    )
  + [""]
  + table(
      [["SUBJECT", "RUNS", "COST", "WORKFLOWS", "CACHE-READ"]]
      + ($runs
         | subjects
         | by_cost
         | .[:20]
         | map([.key, (.n | tostring), cost_cell, .workflows, (.cr | fmt)]))
    )
  + [""]
  + (if $totals.unpriced_runs > 0
     then table(
       [["COST-UNKNOWN", "RUNS", "CACHE-READ", "OUTPUT", "WORKFLOWS"]]
       + ($runs
          | map(select(.unpriced))
          | subjects
          | sort_by(.cr)
          | reverse
          | map([.key, (.n | tostring), (.cr | fmt), (.o | fmt), .workflows]))
     ) + [""]
     else []
     end)
  + table(
      [["RUN", "WORKFLOW", "SUBJECT", "COST", "INPUT", "OUTPUT", "CACHE-CREATE", "CACHE-READ", "TIME"]]
      + ($runs
         | map([
             (.run_id | tostring),
             .workflow,
             (.subject | short),
             (if .unpriced and (.cost_usd == 0 or .cost_usd == null) then "n/a"
              else (.cost_usd | usd) + (.unpriced | floor_marker)
              end),
             (.input_tokens | fmt),
             (.output_tokens | fmt),
             (.cache_creation_input_tokens | fmt),
             (.cache_read_input_tokens | fmt),
             .created_at[:16]
           ]))
    )
  + [""]
  + (($runs | map(.subject) | unique | length) as $count
     | if $count > 20
       then ["Subjects: showing 20 of \($count), ordered by reported cost then cached input; the JSON on stdout has them all."]
       else []
       end)
  + (if $totals.unpriced_runs > 0
     then ["COST-UNKNOWN lists Codex runs (whose cost is not reported) and incomplete Claude runs (whose cost is not recoverable). Their tokens are counted. A '+' marks a cost floor; 'n/a' means no priced runs contributed."]
     else []
     end)
  + (if $totals.skipped_runs > 0
     then ["\($totals.skipped_runs) run(s) uploaded no readable session-logs artifact and are absent entirely: runs that ended before the upload or had torn uploads."]
     else []
     end)
  + ["Claude cost is at API list prices; Codex cost is not reported. Codex cached input is counted as cache-read, not input."]
  | join("\n") + "\n";

. as $input
| ($input.jobs
   | to_entries
   | map(.value + {_order: .key} | claude_shape)
   | group_by(.run_id)
   | map(run_entry)
   | sort_by(.created_at, ._order)
   | group_by(.created_at)
   | reverse
   | (add // [])
   | map(del(._order))) as $runs
| {
    runs: $runs,
    totals: ($runs | {
      input_tokens: sum(.input_tokens),
      output_tokens: sum(.output_tokens),
      cache_creation_input_tokens: sum(.cache_creation_input_tokens),
      cache_read_input_tokens: sum(.cache_read_input_tokens),
      turns: sum(.turns),
      cost_usd: (sum(.cost_usd) | (. + 1e-12) * 100 | round / 100),
      partial_runs: (map(select(.partial)) | length),
      unpriced_runs: (map(select(.unpriced)) | length),
      skipped_runs: $input.skipped
    })
  } as $report
| {report: $report, summary: ($report | summary($input.since))}
"""


def _json_documents(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    documents: list[dict[str, Any]] = []
    position = 0
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text):
            break
        value, position = decoder.raw_decode(text, position)
        if not isinstance(value, dict):
            raise TypeError("token-usage.json holds a non-object JSON value")
        documents.append(value)
    return documents


def _is_session_log(artifact: dict[str, Any]) -> bool:
    return "-session-logs" in artifact["name"]


def _artifacts(path: str) -> list[dict[str, Any]]:
    """The ``artifacts`` array of one listing page, or raise a read failure."""
    body = _common.gh_json("api", path, quiet=True)
    if not isinstance(body, dict) or not isinstance(body.get("artifacts"), list):
        raise _common.UnexpectedShape(f"{path}: no artifacts array")
    return body["artifacts"]


def _session_logs(
    repo: str, created_after: datetime
) -> tuple[list[dict[str, Any]], bool]:
    """The session-log artifacts the repository holds for runs created since
    *created_after*, read from its artifact listing, and whether the listing
    was read to that bound.

    Each artifact names its run, so a run that uploaded no session log costs
    nothing, and most runs in a window uploaded none: a landing check, a relay,
    a mention that was not for the bot. The listing runs newest first, out of
    order by minutes at most, and no artifact predates its run, so the read
    ends at the first page that reaches one created before *created_after*, a
    day before the window opens. A page that fails ends the read early, with
    the pages before it kept.
    """
    found: dict[int, dict[str, Any]] = {}
    page = 1
    while True:
        try:
            artifacts = _artifacts(
                f"repos/{repo}/actions/artifacts?per_page={ARTIFACT_PAGE}&page={page}"
            )
        except _common.GH_READ_FAILED as error:
            stderr = getattr(error, "stderr", None) or str(error)
            print(
                f"WARNING: the artifact listing failed at page {page} "
                f"({stderr.strip()}); asking each run it had not reached for "
                "its own artifacts.",
                file=sys.stderr,
            )
            return list(found.values()), False
        # Keyed by id: an upload during the read shifts the listing a place,
        # repeating an artifact at the head of the next page.
        found.update(
            (artifact["id"], artifact)
            for artifact in artifacts
            if _is_session_log(artifact)
        )
        if len(artifacts) < ARTIFACT_PAGE or any(
            _parse_time(artifact["created_at"]) < created_after
            for artifact in artifacts
        ):
            return list(found.values()), True
        page += 1


def _run_session_logs(repo: str, run_id: int) -> list[dict[str, Any]]:
    """The session-log artifacts of one run, read across every page of its
    artifact listing."""
    found: list[dict[str, Any]] = []
    page = 1
    while True:
        artifacts = _artifacts(
            f"repos/{repo}/actions/runs/{run_id}/artifacts"
            f"?per_page={ARTIFACT_PAGE}&page={page}"
        )
        found.extend(a for a in artifacts if _is_session_log(a))
        if len(artifacts) < ARTIFACT_PAGE:
            return found
        page += 1


def _usage_records(repo: str, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """The token-usage records in one session-log artifact, with its harness."""
    harness = "codex" if artifact["name"].startswith("codex-") else "claude"
    with tempfile.TemporaryFile() as archive:
        _common.gh(
            "api",
            f"repos/{repo}/actions/artifacts/{artifact['id']}/zip",
            stdout=archive,
            quiet=True,
        )
        with zipfile.ZipFile(archive) as files:
            return [
                {**record, "harness": harness}
                for name in files.namelist()
                if PurePosixPath(name).name == "token-usage.json"
                for record in _json_documents(files.read(name).decode())
            ]


def _report(jobs: list[dict[str, Any]], *, skipped: int, since: str) -> dict[str, Any]:
    result = subprocess.run(
        ["jq", "-c", REPORT_JQ],
        input=json.dumps({"jobs": jobs, "skipped": skipped, "since": since}),
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main(completed_after: datetime, extra_prefixes: tuple[str, ...] = ()) -> int:
    since = _stamp(completed_after)
    created_after = completed_after - CREATION_CUSHION
    repo = os.environ.get("TARGET_REPO") or _common.repository()
    repo_args = ["-R", repo]

    workflow_rows = _common.gh_json(
        "workflow", "list", *repo_args, "--limit", str(WORKFLOW_LIMIT), "--json", "name"
    )
    if len(workflow_rows) >= WORKFLOW_LIMIT:
        print(
            f"WARNING: the repository has at least {WORKFLOW_LIMIT} workflows, the "
            "fetch limit — a Tend workflow beyond it is missing from the totals "
            "below entirely.",
            file=sys.stderr,
        )
    prefixes = ["tend-", *extra_prefixes]
    workflows = _common.unique(
        row["name"]
        for row in workflow_rows
        if any(row["name"].startswith(prefix) for prefix in prefixes)
    )

    runs_by_id: dict[int, dict[str, Any]] = {}
    for workflow in workflows:
        try:
            rows = _common.gh_json(
                "run",
                "list",
                *repo_args,
                "--workflow",
                workflow,
                "--created",
                f">={_stamp(created_after)}",
                "--status",
                "completed",
                "--json",
                "databaseId,conclusion,createdAt,updatedAt,workflowName",
                "--limit",
                str(RUN_LIMIT),
                quiet=True,
            )
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            print(
                f"WARNING: 'gh run list' for '{workflow}' failed — its runs are "
                "absent from the totals below.",
                file=sys.stderr,
            )
            rows = []
        if len(rows) >= RUN_LIMIT:
            print(
                f"WARNING: '{workflow}' returned {RUN_LIMIT} runs, the Actions "
                "API's pagination ceiling — older runs in the window are "
                "unreachable and the totals below under-report it. Narrow the "
                "window to bring it under the ceiling; raising RUN_LIMIT cannot help.",
                file=sys.stderr,
            )
        for row in rows:
            # A run whose `if:` skipped every job ran no session, so it has no
            # upload to miss.
            if row.get("conclusion") == "skipped":
                continue
            if _parse_time(row["updatedAt"]) >= completed_after:
                runs_by_id[int(row["databaseId"])] = row

    logs_by_run: dict[int, list[dict[str, Any]]] = {}
    listed, complete = _session_logs(repo, created_after)
    for artifact in listed:
        run_id = (artifact["workflow_run"] or {}).get("id")
        if run_id in runs_by_id:
            logs_by_run.setdefault(run_id, []).append(artifact)
    if not complete:
        # A run the partial listing never reached may still have uploaded one;
        # a failed read here costs that run alone, counted as skipped below.
        for run_id in runs_by_id.keys() - logs_by_run.keys():
            try:
                logs = _run_session_logs(repo, run_id)
            except _common.GH_READ_FAILED:
                continue
            if logs:
                logs_by_run[run_id] = logs

    print(
        f"Downloading {sum(map(len, logs_by_run.values()))} session logs for "
        f"{len(logs_by_run)} of {len(runs_by_id)} runs...",
        file=sys.stderr,
    )
    jobs: list[dict[str, Any]] = []
    skipped = 0
    for run_id, run in runs_by_id.items():
        if run_id not in logs_by_run:
            skipped += 1
            continue
        try:
            run_jobs = [
                record
                for artifact in logs_by_run[run_id]
                for record in _usage_records(repo, artifact)
            ]
            if not run_jobs:
                raise ValueError("no token usage records")
        except (
            subprocess.CalledProcessError,
            OSError,
            TypeError,
            ValueError,
            zipfile.BadZipFile,
        ) as error:
            if not isinstance(error, subprocess.CalledProcessError):
                print(f"{run_id}: unreadable token usage: {error}", file=sys.stderr)
            skipped += 1
            continue
        stamp = {
            "run_id": run_id,
            "workflow": run["workflowName"],
            "created_at": run["createdAt"],
        }
        jobs.extend({**record, **stamp} for record in run_jobs)

    output = _report(jobs, skipped=skipped, since=since)
    _common.dump(output["report"])
    sys.stderr.write(output["summary"])
    return 0
