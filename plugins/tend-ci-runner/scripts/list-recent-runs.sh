#!/usr/bin/env bash
# Lists recently completed tend CI runs.
#
# Fetches runs started in the past 3 hours, then filters to only those that
# are completed and whose updatedAt falls within a 1-hour completion window.
# This two-step approach is needed because `gh run list --created` filters
# by *start* time, not *end* time — a run started 2h ago may have just
# finished, and a run started 50min ago may still be running.
#
# Window anchor: when invoked under a scheduled workflow with a simple
# hourly cron (`MM * * * *`), the completion window is anchored to the most
# recent intended cron tick instead of `now`. Consecutive cycles then tile
# exactly: [intended-1h, intended], then [intended, intended+1h]. Without
# this, GHA scheduler delay (20-40 min during peak hours) shifts each
# cycle's window relative to actual start time and drops runs that finished
# in the slack between consecutive actual starts. For non-schedule events
# or non-hourly crons, falls back to a now-anchored 1h window.
#
# Environment variables:
#   TARGET_REPO - Query a different repo (default: current repo)
#
# Output: JSON array of {databaseId, conclusion, createdAt, updatedAt} objects.

set -euo pipefail

# Prevent gh from emitting ANSI color codes in non-TTY contexts.
export NO_COLOR=1

repo_args=()
if [ -n "${TARGET_REPO:-}" ]; then
  repo_args=(-R "$TARGET_REPO")
fi

# Dynamically discover workflows by prefix. Multiple prefixes supported.
# Usage: ./list-recent-runs.sh [prefix ...]
if [ $# -eq 0 ]; then
  PREFIXES=("tend-")
else
  PREFIXES=("$@")
fi

WORKFLOWS=()
for prefix in "${PREFIXES[@]}"; do
  mapfile -t matches < <(gh workflow list "${repo_args[@]}" --json name --jq ".[].name | select(startswith(\"$prefix\"))")
  WORKFLOWS+=("${matches[@]}")
done

# Detect a simple hourly cron (e.g. "47 * * * *") from the workflow event
# payload so we can anchor the window to the most recent intended tick.
cron_minute=""
if [ -f "${GITHUB_EVENT_PATH:-}" ]; then
  schedule=$(jq -r '.schedule // empty' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
  if [[ "$schedule" =~ ^([0-9]+)\ \*\ \*\ \*\ \*$ ]]; then
    cron_minute="${BASH_REMATCH[1]}"
  fi
fi

if [ -n "$cron_minute" ]; then
  this_hour_tick=$(date -u -d "$(date -u +%Y-%m-%dT%H:00:00) $cron_minute minutes" +%s)
  now_ts=$(date -u +%s)
  if [ "$now_ts" -lt "$this_hour_tick" ]; then
    intended=$((this_hour_tick - 3600))
  else
    intended=$this_hour_tick
  fi
  COMPLETED_AFTER=$((intended - 3600))
  CREATED_SINCE=$(date -u -d "@$((intended - 10800))" +%Y-%m-%dT%H:%M:%S)
else
  CREATED_SINCE=$(date -d '3 hours ago' +%Y-%m-%dT%H:%M:%S)
  COMPLETED_AFTER=$(date -d '1 hour ago' +%s)
fi

all_runs="[]"

for wf in "${WORKFLOWS[@]}"; do
  runs=$(gh run list \
    "${repo_args[@]}" \
    --workflow "${wf}" \
    --created ">=${CREATED_SINCE}" \
    --json databaseId,conclusion,createdAt,updatedAt \
    --limit 50 2>/dev/null || echo "[]")
  all_runs=$(echo "$all_runs" "$runs" | jq -s 'add')
done

# Filter: drop in-progress (empty conclusion), keep only recently finished
echo "$all_runs" | jq --argjson cutoff "$COMPLETED_AFTER" '
  [ .[]
    | select(.conclusion != null and .conclusion != "")
    | select((.updatedAt | fromdateiso8601) >= $cutoff)
  ]
'
