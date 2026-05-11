# Website data

The tend marketing site renders three data streams. Each uses the cheapest
mechanism that meets its freshness budget.

| Stream | Mechanism | Freshness | Where data lives |
| --- | --- | --- | --- |
| Stats | Nightly bot task → static JSON | 24 h | `data/stats.json` on `main` |
| Activity | Nightly bot task → static JSON | 24 h | `data/activity.json` on `main` |
| Currently tending | Cloudflare Worker, 30 s edge cache | 30 s | `currently.tend-src.com` |

The rationale (rate-limit math, why one Worker rather than all-static or
all-dynamic) is in [`../WEBSITE-live-data.md`](../WEBSITE-live-data.md).

## Input: `data/consumers.json`

Each tend-using repo is one entry — produced by `running-tend`'s weekly
refresh.

```json
[
  {"repo": "owner/name", "bot_name": "tend-agent"},
  ...
]
```

Both the daily fetcher and the Worker read `data/consumers.json` (the fetcher
from disk during the Action run; the Worker via
`raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json`, cached
in KV for 1 h).

## Daily static JSON

[`../scripts/fetch_website_data.py`](../scripts/fetch_website_data.py) is a
stdlib-only Python script the tend bot runs each night as part of
`running-tend`'s nightly task list (see
[`../.claude/skills/running-tend/SKILL.md`](../.claude/skills/running-tend/SKILL.md)).
It reads `data/consumers.json`, iterates each entry's `bot_name`, and writes
two narrow JSON files into `data/`. The bot direct-pushes the changes to
`main` — pure data churn that would swamp the review queue if it went
through PRs.

### Multi-bot semantics

Counts are **summed** across bots: `reviews_total` is the union of reviews
authored by *any* tend bot. Activity events from different bots are merged
and deduped by URL, with the first kind seen winning when the same PR
appears in multiple queries (queries run in order: ci-fix → review →
triage).

### Throttling

Search API allows 30 req/min/token. The fetcher self-throttles to ≥2.1 s
between Search calls, so a daily run can scale to ~70 bots without tripping
the limit. With current N=5 it takes ~90 s.

### `activity.json`

```jsonc
{
  "generated_at": "2026-05-10T05:30:00Z",
  "events": [                                       // sorted newest first; capped at 10
    {
      "repo": "max-sixty/tend",
      "kind": "review",                             // "review" | "triage" | "ci-fix"
      "title": "feat: add foo support",
      "url": "https://github.com/max-sixty/tend/pull/123",
      "at": "2026-05-09T14:22:00Z"                  // issue/PR updated_at
    }
  ]
}
```

| `kind`   | Source query                                          |
| -------- | ----------------------------------------------------- |
| `ci-fix` | `author:<bot> is:pr`                                  |
| `review` | `commenter:<bot> is:pr -author:<bot>`                 |
| `triage` | `commenter:<bot> is:issue`                            |

### `stats.json`

```jsonc
{
  "generated_at": "2026-05-10T05:30:00Z",
  "reviews_total": 1199,
  "reviews_this_week": 111,
  "ci_fixes_total": 944,
  "ci_fixes_this_week": 55,
  "triage_comments_total": 331
}
```

All counts come from the Search API's `total_count`. "This week" means the
last 7 days by issue/PR `updated`.

### No-op skip

`write_if_changed` compares the new payload's structural content (everything
except `generated_at`) against the existing file. Identical content skips
the write, so the daily cron only commits when stats actually moved.

## Currently tending

Served by a Cloudflare Worker — see
[`../worker/README.md`](../worker/README.md) for setup. The endpoint returns:

```jsonc
{
  "generated_at": "2026-05-10T17:30:00Z",
  "currently_tending": [
    {
      "repo": "max-sixty/tend",
      "workflow": "tend-review",
      "started_at": "2026-05-10T17:29:14Z",
      "run_url": "https://github.com/max-sixty/tend/actions/runs/12345"
    }
  ]
}
```

Updates to `consumers.json` propagate to the Worker within ~1 h (the KV
cache TTL on the repos lookup).

### UI fallback contract

When `currently_tending` is empty, or the Worker request fails, the UI
should fall back to showing the most recent event from `activity.json` as
"last action N min ago" — the indicator never breaks the page. This
fallback lives in the rendering layer, not in the data layer.

## Inputs and outputs

```
.github/workflows/tend-nightly.yaml         (the bot's nightly run)
  └─ running-tend skill instructs the bot to run:
     scripts/fetch_website_data.py
     and commit data/{activity,stats}.json to main

.github/workflows/publish-site.yaml         on push to main site/**
  └─ builds + deploys site/ (Astro) to GitHub Pages

.github/workflows/worker-deploy.yaml        on push to main worker/**
  └─ deploys worker/ to Cloudflare

Cloudflare Worker (tend-currently)
  ├─ reads data/consumers.json via raw URL (KV-cached 1 h)
  ├─ fans out to actions/runs per repo
  └─ serves CORS-enabled JSON at https://currently.tend-src.com
     (rendered response edge-cached 30 s; 5 s on fallback)
```

## Local development

Fetcher:

```sh
GITHUB_TOKEN=$(gh auth token) python3 scripts/fetch_website_data.py \
  --consumers-file data/consumers.json --out-dir /tmp/out
```

Worker (see `../worker/README.md` for full setup):

```sh
cd worker
npm install
echo "GITHUB_TOKEN=$(gh auth token)" > .dev.vars
npm run dev      # http://localhost:8787
```
