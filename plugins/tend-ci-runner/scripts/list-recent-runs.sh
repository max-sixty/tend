#!/usr/bin/env bash
# Lists recently completed tend CI runs.
#
# Fetches runs started in the past 3 hours, then filters to only those that
# are completed and whose updatedAt is within the past 2 hours. This
# two-step approach is needed because `gh run list --created` filters by
# *start* time, not *end* time — a run started 2h ago may have just
# finished, and a run started 50min ago may still be running.
#
# The 2h completion window absorbs GitHub Actions cron delay. Hourly
# review-reviewers runs (cron `47 * * * *`) routinely fire 20–40 min late
# during peak hours, producing gaps of 80–100 min between consecutive
# cycles. A tighter 1h cutoff drops everything older than the previous
# cycle's expected start, silently hiding runs that landed in the delay
# slack. The 2h cutoff covers all observed gaps with margin; the small
# re-analysis overlap between cycles is cheap because gist entries are
# keyed by run ID.
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

CREATED_SINCE=$(date -d '3 hours ago' +%Y-%m-%dT%H:%M:%S)
COMPLETED_AFTER=$(date -d '2 hours ago' +%s)

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
