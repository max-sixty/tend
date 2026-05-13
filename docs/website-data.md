# Website data

Both data streams the tend site renders are served by one Cloudflare Worker.
The Worker reads a small input file (`data/consumers.json`) from this repo, fans
out to GitHub, and serves CORS-enabled JSON to the site.

| Stream | Endpoint | Freshness budget | Fallback budget |
| --- | --- | --- | --- |
| Currently tending | `/currently-tending` (also `/`) | 30 s | 5 s |
| Activity | `/activity` | 5 min | 30 s |

Base URL: `https://api.tend-src.com`.

## Why a Worker, not browser-direct

Unauthenticated GitHub REST is 60 req/hour/IP and the Search API is 10
req/min/IP — both shared across everyone behind a NAT. A single
currently-tending poll fans out one `actions/runs` call per consumer repo every
30 s; `/activity` fans out one Search query per bucket per bot. One browser tab
would exhaust those quotas in minutes. The Worker holds an authenticated token
(5,000 req/hour, 30 Search req/min) and caches each route at the colo, so
origin load is bounded by the freshness budget, not by viewer count. Static
nightly JSON would cover
`/activity` but can't meet currently-tending's sub-minute freshness budget, so
both live on the one Worker.

## Input: `data/consumers.json`

Each tend-using repo is one entry — produced by `running-tend`'s weekly
refresh.

```json
[
  {"repo": "owner/name", "bot_name": "tend-agent"},
  ...
]
```

The Worker fetches this via
`raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json` and
caches it in KV for 1 h, so a `consumers.json` commit propagates within
the hour.

## Multi-bot semantics

Everything is **merged across bots**: each `/activity` bucket sums `count` and
`count_this_week` over all tend bots, and its `recent` list is the union of all
bots' recent items, sorted newest-first; `currently_tending` is the union of all
bots' in-progress runs. Activity is *not* scoped to consumer repos — `count`
comes from Search's `total_count`, which can't be filtered post-hoc — but a tend
bot only acts in its own repo, so this is a distinction without a difference in
practice.

## Caching

Stale-while-revalidate, in the Worker's colo cache. Every request is
answered from cache — no waiting on the GitHub fanout. Each route has a
**freshness budget** (`/currently-tending` 30 s, `/activity` 5 min); once a
cached entry is older than that, the next request still gets the cached
copy *and* triggers a background refresh, so the entry following it is
fresh. An entry stays serveable for 10 freshness budgets — 5 min on
`/currently-tending`, 50 min on `/activity` — before the cache drops it, so
a viewer never sees data older than that and an ordinarily-trafficked site
never goes cold.

When a refresh throws (GitHub outage), the empty payload is cached with a
short **fallback budget** (5 s / 30 s) so the next request retries soon
rather than locking in an empty response.

Still demand-driven: a background refresh only fires when a request comes
in, so a no-traffic day costs zero GitHub calls. The cost is one cold
start — a fresh deploy, or a gap longer than 10 freshness budgets, makes
that one request wait on the fanout. A cron-triggered prewarm would close
even that gap but would trade away the zero-when-idle property; not worth
it at this scale.

## Endpoint shapes

### `/currently-tending`

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

Source: `GET /repos/{owner}/{repo}/actions/runs?status=in_progress` per
consumer, filtered to workflows whose `name` starts with `tend-`.

**UI fallback:** when `currently_tending` is empty or the Worker request
fails, the UI falls back to showing "last tended N min ago" from the most
recent item in `/activity` — the indicator never breaks the page. This
fallback lives in the rendering layer (`site/src/components/CurrentlyTending.astro`),
not the data layer.

### `/activity`

Recent things tend has done, in primitive buckets — one Search query per bucket
per bot (`sort=updated`): the page yields both the `recent` items and the
lifetime `count` (`total_count`); `count_this_week` is counted off the page, so
it saturates around one page (~100) per bot per bucket — fine for a headline
number. The fanout is 3·N concurrent Search requests, which stays under the
30 req/min cap up to ~10 bots; past that, the per-bot calls would need
staggering or a scheduled refresh (see the Phase-2 note).

```jsonc
{
  "generated_at": "2026-05-10T17:30:00Z",
  "prs":      { "count": 980,  "count_this_week": 12, "recent": [ /* RecentItem */ ] },
  "issues":   { "count": 41,   "count_this_week": 1,  "recent": [ ... ] },
  "comments": { "count": 1530, "count_this_week": 36, "recent": [ ... ] }
}
```

`RecentItem` = `{ repo, title, url, at }` (`at` is the issue/PR `updated_at`),
newest-first, ≤10 per bucket.

| bucket | Search query | "the bot …" |
| --- | --- | --- |
| `prs` | `author:<bot> is:pr` | opened these PRs (any state) |
| `issues` | `author:<bot> is:issue` | opened these issues — mostly tend's own trackers (missing PAT scopes, "nightly tests failed", `tend-outage`), so this bucket leans "tend flagged a problem" |
| `comments` | `commenter:<bot> -author:<bot>` | chimed in on these PRs/issues (not its own) |

> **TODO — Phase 2:** a consumer (a scheduled job, or the Worker calling Claude)
> reads `/activity` and writes a short prose summary of what tend's been up to;
> the summary lives in KV and is what the site renders. If that summary wants a
> longer span than the last week (beyond GitHub's ~90-day events window or one
> Search page), that's when a KV/D1 accumulator that appends activity as it
> arrives earns its keep — until then, demand-fetch is cheap enough.

## Topology

```
data/consumers.json on main
  └─ refreshed weekly by running-tend's `weekly` task (PR-gated)

.github/workflows/publish-site.yaml         on push to main site/**
  └─ builds + deploys site/ (Astro) to GitHub Pages

.github/workflows/worker-deploy.yaml        on push to main worker/**
  └─ deploys worker/ to Cloudflare

Cloudflare Worker (tend-website)
  ├─ reads data/consumers.json via raw URL (KV-cached 1 h)
  ├─ /currently-tending: fans out actions/runs per repo (in-progress, tend-*)
  ├─ /activity:          fans out one Search query per bucket per bot
  └─ each route stale-while-revalidate from the colo cache, served at api.tend-src.com
```

## Local development

```sh
cd worker
npm install
echo "GITHUB_TOKEN=$(gh auth token)" > .dev.vars
npm run dev      # http://localhost:8787
```

Then `curl http://localhost:8787/activity` etc.
