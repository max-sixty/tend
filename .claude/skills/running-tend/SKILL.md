---
name: running-tend
description: Tend-specific guidance for tend CI workflows. Adds non-standard workflow inclusion for usage analysis and repo conventions on top of the generic tend-* skills.
metadata:
  internal: true
---

# Tend CI

Repo-specific guidance for tend workflows running on tend itself. The generic
skills (`tend-running-in-ci`, `tend-review`, `tend-triage`, etc.) provide the
workflow framework; this skill adds tend conventions.

## Non-standard workflows

Tend has Claude-powered workflows beyond the generated `tend-*` set:

| Workflow | File | Schedule | Purpose |
|----------|------|----------|---------|
| `review-reviewers` | `review-reviewers.yaml` | `47 * * * *` | Hourly analysis of adopter repo sessions |

These use the `tend@v1` action and produce `claude-session-logs*` artifacts,
but their names don't match the `tend-*` prefix that scripts filter on by
default.

### Usage analysis

Pass extra prefixes when running token reports or listing runs so these
workflows are included:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/token-report.sh" 24 "review-"
TARGET_REPO=max-sixty/tend "${CLAUDE_PLUGIN_ROOT}/scripts/list-recent-runs.sh" "tend-" "review-"
```

## Labels

- `claude-behavior` — findings from `review-reviewers`
- `review-runs` — findings from `review-runs`

## Session Log Paths

Artifact paths: `-home-runner-work-tend-tend/<session-id>.jsonl`

`review-reviewers` runs produce 3 session logs per run (one per matrix repo:
`max-sixty/worktrunk`, `max-sixty/tend`, `PRQL/prql`).

## Weekly: refresh `data/consumers.json`

Public repos that have installed tend. Read by the website build (see
`WEBSITE.md`) for the stat strip and activity feed; needs no opt-in because
the workflow files are public.

```bash
# 1. Discover consumer repos via code search. `max-sixty/tend@v1` only
#    appears in generated tend-*.yaml workflow files.
mapfile -t REPOS < <(
  gh search code 'max-sixty/tend@v1' --limit 100 --json repository,path \
    | jq -r '.[] | select(.path | startswith(".github/workflows/tend-")) | .repository.nameWithOwner' \
    | sort -u
)

# 2. Resolve bot_name from each repo's .config/tend.toml.
mkdir -p data
{
  for repo in "${REPOS[@]}"; do
    bot=$(gh api "repos/$repo/contents/.config/tend.toml" --jq '.content' 2>/dev/null \
      | base64 -d 2>/dev/null \
      | sed -n 's/^bot_name *= *"\([^"]*\)".*/\1/p' | head -1)
    [ -n "$bot" ] || continue
    jq -nc --arg repo "$repo" --arg bot "$bot" '{repo: $repo, bot_name: $bot}'
  done
} | jq -s . > data/consumers.json
```

Open a PR titled `chore: refresh consumers.json` if the file changed. Skip
the PR (no diff to land) when `git status --porcelain data/consumers.json`
is empty — `git diff --quiet` returns 0 for untracked paths, so the
first-run case would no-op. Code search is 10 req/min — one call covers
the whole list.
