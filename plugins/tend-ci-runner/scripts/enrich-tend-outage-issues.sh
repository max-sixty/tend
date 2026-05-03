#!/usr/bin/env bash
# Enriches open tend-outage issues with failure details from referenced runs.
#
# action.yaml's "Report failure" step records only the workflow run link —
# error annotations and job logs are not reliably available while the job is
# in_progress, so the action can't extract them at the time of failure.
#
# This script runs nightly: for each open tend-outage issue, it finds run IDs
# in the body and comments, fetches failure annotations for each failed job
# in those runs, and posts a comment with the details. Already-processed
# runs are skipped via an `<!-- enriched-run:RUN_ID -->` marker in prior
# comments — the marker is posted even when no annotations were found, so
# unenrichable runs aren't retried every night.

set -euo pipefail

LABEL="tend-outage"
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')

gh issue list --label "$LABEL" --state open --json number --jq '.[].number' \
  | while read -r ISSUE; do
    RAW=$(gh issue view "$ISSUE" --repo "$REPO" --json body,comments)

    REFERENCED=$(echo "$RAW" | jq -r '
      [.body, (.comments[].body)] | .[] | scan("/actions/runs/([0-9]+)")[0]
    ' | sort -u)
    ENRICHED=$(echo "$RAW" | jq -r '
      .comments[].body | scan("<!-- enriched-run:([0-9]+) -->")[0]
    ' | sort -u)

    comm -23 <(echo "$REFERENCED") <(echo "$ENRICHED") \
      | while read -r RUN_ID; do
        [ -z "$RUN_ID" ] && continue

        : > /tmp/enrich-errors.md
        # Capture jobs first so a 404 (deleted/expired run) doesn't trip
        # `set -e` via the pipe's exit status.
        JOBS=$(gh api "repos/$REPO/actions/runs/$RUN_ID/jobs" \
          --jq '.jobs[] | select(.conclusion == "failure") | "\(.id)\t\(.name)"' \
          2>/dev/null || true)
        while IFS=$'\t' read -r JOB_ID JOB_NAME; do
          [ -z "$JOB_ID" ] && continue
          MSG=$(gh api "repos/$REPO/check-runs/$JOB_ID/annotations" \
            --jq '[.[] | select(.annotation_level == "failure") | .message
                  | select(test("^Process completed") | not)] | join("\n\n")' \
            2>/dev/null || true)
          [ -n "$MSG" ] && printf '### %s\n\n```\n%s\n```\n\n' "$JOB_NAME" "$MSG" \
            >> /tmp/enrich-errors.md
        done <<< "$JOBS"

        RUN_URL="https://github.com/$REPO/actions/runs/$RUN_ID"
        if [ -s /tmp/enrich-errors.md ]; then
          {
            echo "Error details for [run $RUN_ID]($RUN_URL):"
            echo
            cat /tmp/enrich-errors.md
            echo "<!-- enriched-run:$RUN_ID -->"
          } > /tmp/enrich-comment.md
        else
          {
            echo "No failure details could be extracted for [run $RUN_ID]($RUN_URL)."
            echo "<!-- enriched-run:$RUN_ID -->"
          } > /tmp/enrich-comment.md
        fi
        gh issue comment "$ISSUE" --repo "$REPO" -F /tmp/enrich-comment.md
      done
  done
