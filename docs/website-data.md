# Website data

All three data streams the tend site renders are served by one Cloudflare
Worker. The Worker reads a small input file (`data/consumers.json`) from
this repo, fans out to GitHub, and serves CORS-enabled JSON to the site.

| Stream | Endpoint | Edge TTL | Fallback TTL |
| --- | --- | --- | --- |
| Currently tending | `/currently-tending` (also `/`) | 30 s | 5 s |
| Activity | `/activity` | 5 min | 30 s |
| Stats | `/stats` | 1 h | 60 s |

Base URL: `https://currently.tend-src.com`. The Worker exists at the
`currently.*` subdomain for historical reasons (it started as a single-
purpose currently-tending endpoint); renaming to `api.*` is a deploy-time
decision, not a code change.

The rate-limit reasoning is in [`../WEBSITE-live-data.md`](../WEBSITE-live-data.md).

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

Counts are **summed** across bots: `reviews_total` is the union of reviews
authored by *any* tend bot. Activity events are merged and deduped by URL,
with the first kind seen winning when the same PR appears in multiple
queries (declared order: ci-fix → review → triage).

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
fails, the UI should fall back to showing the most recent event from
`/activity` as "last action N min ago" — the indicator never breaks the
page. The fallback lives in the rendering layer, not the data layer.

### `/activity`

```jsonc
{
  "generated_at": "2026-05-10T17:30:00Z",
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

Source — 3 queries × N bots, deduped by URL:

| `kind`   | Source query                                          |
| -------- | ----------------------------------------------------- |
| `ci-fix` | `author:<bot> is:pr`                                  |
| `review` | `commenter:<bot> is:pr -author:<bot>`                 |
| `triage` | `commenter:<bot> is:issue`                            |

### `/stats`

```jsonc
{
  "generated_at": "2026-05-10T17:30:00Z",
  "reviews_total": 1199,
  "reviews_this_week": 111,
  "ci_fixes_total": 944,
  "ci_fixes_this_week": 55,
  "triage_comments_total": 331
}
```

All counts come from the Search API's `total_count`. "This week" means the
last 7 days by issue/PR `updated`.

## Topology

```
data/consumers.json on main
  └─ refreshed weekly by running-tend's `weekly` task (PR-gated)

.github/workflows/publish-site.yaml         on push to main site/**
  └─ builds + deploys site/ (Astro) to GitHub Pages

.github/workflows/worker-deploy.yaml        on push to main worker/**
  └─ deploys worker/ to Cloudflare

Cloudflare Worker (tend-currently)
  ├─ reads data/consumers.json via raw URL (KV-cached 1 h)
  ├─ /currently-tending: fans out to actions/runs per repo
  ├─ /activity:          fans out 3 Search queries per bot, dedupes
  ├─ /stats:             5 Search queries per bot, sums total_count
  └─ each route edge-cached at its own TTL
```

## Local development

```sh
cd worker
npm install
echo "GITHUB_TOKEN=$(gh auth token)" > .dev.vars
npm run dev      # http://localhost:8787
```

Then `curl http://localhost:8787/activity` etc.
