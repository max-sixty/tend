# Website data

The tend marketing site renders three data streams. Each uses the cheapest
mechanism that meets its freshness budget.

| Stream | Mechanism | Freshness | Where data lives |
| --- | --- | --- | --- |
| Stats | Daily Action → static JSON | 24 h | `static/data/stats.json` on `website` |
| Activity | Daily Action → static JSON | 24 h | `static/data/activity.json` on `website` |
| Currently tending | Cloudflare Worker, 30 s KV cache | 30 s | `tend-currently.<sub>.workers.dev` |

The rationale (rate-limit math, why one Worker rather than all-static or
all-dynamic) is in [`../WEBSITE-live-data.md`](../WEBSITE-live-data.md).

## Daily static JSON

[`../scripts/fetch_website_data.py`](../scripts/fetch_website_data.py) is a
stdlib-only Python script invoked by the `website-data` workflow once a day.
It checks out the `website` branch, regenerates the two JSON files under
`static/data/`, and commits if anything changed.

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

Items appearing in multiple queries are deduped by URL; the first kind seen
wins (queries run in the order above).

### `stats.json`

```jsonc
{
  "generated_at": "2026-05-10T05:30:00Z",
  "reviews_total": 412,
  "reviews_this_week": 18,
  "ci_fixes_total": 89,
  "ci_fixes_this_week": 3,
  "triage_comments_total": 67
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

The Worker reads `repos.json` (the discovery script's output) from the
`website` branch via `raw.githubusercontent.com`, cached for 1 h in KV. So
updates to the repo list take effect within an hour without redeploying the
Worker.

### UI fallback contract

When `currently_tending` is empty, or the Worker request fails, the UI
should fall back to showing the most recent event from `activity.json` as
"last action N min ago" — the indicator never breaks the page. This
fallback lives in the rendering layer (phase 3 §11), not in the data
layer.

## Inputs and outputs

```
.github/workflows/website-data.yaml         daily cron
  ├─ reads scripts/fetch_website_data.py    (from main)
  ├─ checks out website branch
  └─ commits website:static/data/{activity,stats}.json

.github/workflows/worker-deploy.yaml        on push to main worker/**
  └─ deploys worker/ to Cloudflare

repos.json                                  produced by discovery script
  ├─ lives at website:static/data/repos.json
  ├─ read by Worker via raw.githubusercontent.com
  └─ not consumed by fetch_website_data.py — activity/stats are bot-scoped
     by Search API, not repo-scoped

Cloudflare Worker (tend-currently)
  ├─ reads repos.json (raw URL, KV-cached 1 h)
  ├─ fans out to actions/runs per repo (KV-cached 30 s)
  └─ serves CORS-enabled JSON
```

## Local development

Fetcher:

```sh
GITHUB_TOKEN=$(gh auth token) python3 scripts/fetch_website_data.py --out-dir ./out
```

Worker (see `../worker/README.md` for full setup):

```sh
cd worker
npm install
echo "GITHUB_TOKEN=$(gh auth token)" > .dev.vars
npm run dev      # http://localhost:8787
```
