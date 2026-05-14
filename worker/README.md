# tend-website Worker

Cloudflare Worker that serves the two data streams the tend marketing site
renders. Reads `data/consumers.json` from this repo, fans out to GitHub, and
serves CORS-enabled JSON.

| Stream | Endpoint | Freshness budget | Fallback budget |
| --- | --- | --- | --- |
| Currently tending | `/currently-tending` (also `/`) | 30 s | 5 s |
| Activity | `/activity` | 5 min | 30 s |

Base URL: `https://api.tend-src.com`. `Access-Control-Allow-Origin: *`
(public read-only data).

## Why a Worker, not browser-direct

Unauthenticated GitHub REST is 60 req/hour/IP and the Search API is 10
req/min/IP — both shared across everyone behind a NAT. A single
currently-tending poll fans out one `actions/runs` call per consumer repo every
30 s; `/activity` fans out one Search query per bucket per bot. One browser tab
would exhaust those quotas in minutes. The Worker holds an authenticated token
(5,000 req/hour, 30 Search req/min) and caches each route at the colo, so
origin load is bounded by the freshness budget, not by viewer count. Static
nightly JSON would cover `/activity` but can't meet currently-tending's
sub-minute freshness budget, so both live on the one Worker.

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

Recent things tend has done, in primitive buckets — one Search query per
bucket per bot (`sort=updated`): the page yields both the `recent` items and
the lifetime `count` (`total_count`); `count_this_week` is counted off the
page, so it saturates around one page (~100) per bot per bucket — fine for a
headline number. The fanout is 4·N concurrent Search requests, which stays
under the 30 req/min cap up to ~7 bots; past that, the per-bot calls would
need staggering or a scheduled refresh (see the Phase-2 note).

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

For `reviews` and `comments`, `url` deep-links to the bot's latest inline
review comment (`…#discussion_r<id>`) or conversation comment
(`…#issuecomment-<id>`), so clicking lands on tend's actual action rather
than the top of the thread. The Worker does one extra GitHub REST call per
recent item to resolve the anchor (`/repos/{repo}/pulls/{n}/comments` for
reviews — the inline-comment endpoint, since tend's reviews are
`COMMENTED` with empty bodies and the review anchor scrolls nowhere;
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

### Multi-bot semantics

Everything is **merged across bots**: each `/activity` bucket sums `count` and
`count_this_week` over all tend bots, and its `recent` list is the union of all
bots' recent items, sorted newest-first; `currently_tending` is the union of all
bots' in-progress runs. Activity is *not* scoped to consumer repos — `count`
comes from Search's `total_count`, which can't be filtered post-hoc — but a tend
bot only acts in its own repo, so this is a distinction without a difference in
practice.

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

## Caching

Responses are served **stale-while-revalidate** from the colo cache
(`caches.default`). A request is always answered from cache — no waiting on
the GitHub fanout. When the cached entry is past its freshness budget
(30 s / 5 min), the hit also kicks off a background refresh via
`ctx.waitUntil` so the next viewer sees fresher data. An entry stays
serveable for ten freshness budgets — 5 min on `/currently-tending`, 50 min
on `/activity` — before the cache drops it, so a viewer never sees data
older than that and an ordinarily-trafficked site never goes cold.

Still demand-driven: a background refresh only fires when a request comes
in, so a no-traffic day costs zero GitHub calls. The cost is one cold start
— a fresh deploy, or a gap longer than 10 freshness budgets, makes that one
request wait on the fanout. A cron-triggered prewarm would close even that
gap but would trade away the zero-when-idle property; not worth it at this
scale.

When a refresh throws (GitHub outage), the empty payload is cached with a
short **fallback budget** (5 s / 30 s) so the next request retries soon
rather than locking in an empty response.

## Topology

```
data/consumers.json on main
  └─ refreshed weekly by running-tend's `weekly` task (PR-gated)

.github/workflows/worker-deploy.yaml          on push to main worker/**
  └─ deploys worker/ to Cloudflare

Cloudflare Worker (tend-website)
  ├─ reads data/consumers.json via raw URL (KV-cached 1 h)
  ├─ /currently-tending: fans out actions/runs per repo (in-progress, tend-*)
  ├─ /activity:          fans out one Search query per bucket per bot
  └─ each route stale-while-revalidate from the colo cache, served at api.tend-src.com
```

## One-time setup (already done)

```sh
npm install
npx wrangler login                                  # opens browser
npx wrangler kv namespace create CACHE              # prints the id
#   → paste the id into wrangler.toml ([[kv_namespaces]] id)
npx wrangler secret put GITHUB_TOKEN                # paste a read-only PAT
npx wrangler deploy                                 # first deploy
```

The PAT needs `actions:read` + `metadata:read` on public repos. After first
deploy, CI handles subsequent deploys via
[`../.github/workflows/worker-deploy.yaml`](../.github/workflows/worker-deploy.yaml),
which authenticates with the `CLOUDFLARE_API_TOKEN` repo secret. That secret
is a scoped token named `tend-ci-worker-deploy` (Workers Scripts + KV + Routes
edit), generated to keep the account's Global API Key out of CI; regenerate at
<https://dash.cloudflare.com/profile/api-tokens> with the "Edit Cloudflare
Workers" template if it's ever lost.

## Local development

```sh
echo "GITHUB_TOKEN=$(gh auth token)" > .dev.vars   # one-time
npm install
npm run dev        # wrangler dev with hot reload at http://localhost:8787
npm test           # unit tests (vitest, no Worker runtime needed)
npm run typecheck
```

Then `curl http://localhost:8787/activity` etc. `wrangler dev` reads the same
`wrangler.toml`; `.dev.vars` is gitignored.

## Cache strategy

- `caches.default` (Cloudflare's colo cache), keyed by the normalized request
  URL — stores the rendered response. The browser revalidates after the
  freshness budget (`Cache-Control: max-age`); the colo cache, a shared
  cache, retains the entry for ten freshness budgets (`s-maxage`) so SWR
  keeps working between viewers. A `x-tend-stale-at` header on the cached
  response tells `serveCached` when to background-refresh a hit. A missing
  or garbled stamp counts as stale, so the first hit self-heals an entry
  written by code predating this scheme.
- `CACHE` KV namespace, key `repos:v1`, TTL 1 h — the `consumers.json`
  content. Decouples `running-tend`'s weekly refresh from Worker deploys. KV
  is appropriate here because the 1 h TTL is above KV's 60 s minimum and we
  want cross-isolate sharing.

Concurrent stale-hits are coalesced: the first request pushes the
cached entry's `x-tend-stale-at` forward by a short grace window
(`REFRESH_GRACE_MS`, currently 30 s) and starts the background refresh;
viewers arriving within that window read the bumped entry as fresh and
skip starting their own refresh. One refresh per stale window per colo,
not one per viewer — keeps the Search-API fanout bounded under bursts.

A cold cache miss costs the route's full fanout (N actions/runs calls for
`/currently-tending`, 4·N Search calls for `/activity`). The freshness
budget bounds how often that happens: at most one cold refresh per budget
per colo, under any traffic level.
