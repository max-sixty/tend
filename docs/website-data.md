# Website data

Both data streams the tend site renders are served by one Cloudflare Worker.
The Worker reads a small input file (`data/consumers.json`) from this repo, fans
out to GitHub, and serves CORS-enabled JSON to the site.

| Stream | Endpoint | Edge TTL | Fallback TTL |
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
(5,000 req/hour, 30 Search req/min) and edge-caches each route, so origin load
is bounded by the TTL, not by viewer count. Static nightly JSON would cover
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

Each Worker route has two TTLs: the normal cache window, and a short
fallback window applied when the refresh throws. The fallback window
ensures a transient GitHub outage clears within seconds rather than locking
in an empty response for the full normal TTL.

Cache is demand-driven — nothing runs on a schedule. The first request in
a TTL window pays the full origin cost; subsequent requests inside that
window hit the edge cache. A no-traffic day costs zero GitHub calls.

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
number. The fanout is 4·N concurrent Search requests, which stays under the
30 req/min cap up to ~7 bots; past that, the per-bot calls would need
staggering or a scheduled refresh (see the Phase-2 note).

```jsonc
{
  "generated_at": "2026-05-10T17:30:00Z",
  "prs":      { "count": 485,  "count_this_week": 6,  "recent": [ /* RecentItem */ ] },
  "issues":   { "count": 82,   "count_this_week": 2,  "recent": [ ... ] },
  "reviews":  { "count": 1102, "count_this_week": 28, "recent": [ ... ] },
  "comments": { "count": 206,  "count_this_week": 4,  "recent": [ ... ] }
}
```

`RecentItem` = `{ repo, title, url, at }`. `at` is the parent issue/PR's
`updated_at`, and `title` is the parent's title — Search returns the item, not
the comment or review body. Newest-first, ≤10 per bucket.

For `reviews` and `comments`, `url` deep-links to the bot's specific
review (`…#pullrequestreview-<id>`) or comment (`…#issuecomment-<id>`), so
clicking lands on tend's actual action rather than the top of the thread.
The Worker does one extra GitHub REST call per recent item to resolve the
anchor (`/repos/{repo}/pulls/{n}/reviews` for reviews,
`/repos/{repo}/issues/{n}/comments` for comments) and falls back to the
parent URL if the follow-up fails. For `prs` and `issues`, `url` is the
parent issue/PR — that is what the bot created.

| bucket | Search query | "the bot …" |
| --- | --- | --- |
| `prs` | `author:<bot> is:pr` | opened these PRs (any state) |
| `issues` | `author:<bot> is:issue` minus four bookkeeping labels (`tend-outage`, `review-runs-tracking`, `review-reviewers-tracking`, `nightly-cleanup`) | opened these issues, filed against the repo — tend's own outage and tracking issues are excluded |
| `reviews` | `reviewed-by:<bot>` | reviewed these PRs (approve / request-changes / review comment) — by volume, tend's main action |
| `comments` | `commenter:<bot> -author:<bot> -reviewed-by:<bot>` | commented on these PRs/issues — excludes its own threads and items already in `reviews` |

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
  └─ each route edge-cached at its own TTL, served at api.tend-src.com
```

## Local development

```sh
cd worker
npm install
echo "GITHUB_TOKEN=$(gh auth token)" > .dev.vars
npm run dev      # http://localhost:8787
```

Then `curl http://localhost:8787/activity` etc.
