# Website data

All three data streams the tend site renders are served by one Cloudflare
Worker. The Worker reads a small input file (`data/consumers.json`) from
this repo, fans out to GitHub, and serves CORS-enabled JSON to the site.

| Stream | Endpoint | Edge TTL | Fallback TTL |
| --- | --- | --- | --- |
| Currently tending | `/currently-tending` (also `/`) | 30 s | 5 s |
| Activity | `/activity` | 5 min | 30 s |
| Stats | `/stats` | 1 h | 60 s |

Base URL: `https://api.tend-src.com`.

## Why a Worker, not browser-direct

Unauthenticated GitHub REST is 60 req/hour/IP. A single currently-tending
poll fans out one `actions/runs` call per consumer repo every 30 s, so one
browser tab would exhaust the IP quota in under a minute — and the Search
API used by `/stats` (and `/activity`'s merged-PR lookups) is capped at 10
req/min/IP unauthenticated, shared across everyone behind a NAT. The Worker
holds an authenticated token (5,000 req/hour, 30 Search req/min) and edge-
caches each route, so origin load is bounded by the TTL, not by viewer
count. Static nightly JSON would cover `/stats` and `/activity` but can't
meet currently-tending's sub-minute freshness budget, so all three live on
the one Worker for consistency.

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
authored by *any* tend bot. Activity events are merged across bots: each
bot's event timeline and merged-PR search contribute rows, comments and
pushes to the same PR/branch collapse into one row with a `count`, and
same-kind rows for the same URL dedup (newest wins). A single PR
legitimately appears under several kinds — `pr-opened`, then `pr-commented`,
then `pr-merged` — those are distinct rows, not duplicates.

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
fails, the UI falls back to showing the most recent event from `/activity`
as "last tended N min ago" — the indicator never breaks the page. This
fallback lives in the rendering layer (`site/src/components/CurrentlyTending.astro`),
not the data layer.

### `/activity`

Recent things tend has *done* — built from each bot's public event timeline
(`GET /users/<bot>/events/public`, one cheap REST call per bot, already
discriminated by event type) plus one Search query per bot for merged PRs
(`author:<bot> is:pr is:merged` — the merge is usually performed by a human,
so it isn't in the bot's own event stream). Merged across bots, collapsed,
sorted newest-first, capped at 40.

```jsonc
{
  "generated_at": "2026-05-10T17:30:00Z",
  "events": [                                       // sorted newest first; capped at 40
    {
      "repo": "max-sixty/tend",
      "kind": "pr-commented",
      "title": "fix: race in cache TTL",
      "url": "https://github.com/max-sixty/tend/pull/441",
      "at": "2026-05-10T16:02:00Z",
      "detail": { "count": 6 }                      // kind-specific (see below); absent for kinds that carry none
    }
  ]
}
```

| `kind`            | Meaning                                                      | Source / `detail`                                                                 |
| ----------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `pr-opened`       | tend opened a PR (CI fix, issue fix, maintenance, workflow self-edit, …) | `PullRequestEvent` action=opened · `detail.category`: `ci-fix`\|`issue-fix`\|`workflow`\|`maintenance`\|`other`, inferred from the head-branch name |
| `pr-merged`       | a tend-authored PR shipped                                    | Search `author:<bot> is:pr is:merged` (`at` = PR `closed_at` ≈ merge time)         |
| `pr-reviewed`     | tend approved or requested changes on a PR                    | `PullRequestReviewEvent` · `detail.verdict`: `approved`\|`changes_requested`       |
| `pr-commented`    | tend left comments on a PR (review bodies, inline, conversation) | `PullRequestReviewEvent`(commented) / `PullRequestReviewCommentEvent` / `IssueCommentEvent` on a PR, collapsed per PR · `detail.count` |
| `pr-commits`      | tend pushed commits to a PR branch (review fixes, conflict resolution) | `PushEvent` to a non-default branch, collapsed per branch · `detail.count`; `url` = head commit |
| `issue-commented` | tend commented on an issue (triage, mention answer)           | `IssueCommentEvent` on an issue, collapsed per issue · `detail.count`             |
| `issue-closed`    | tend closed a resolved issue                                  | `IssuesEvent` action=closed                                                       |
| `dep-approved`    | tend cleared a dependency bump                                | `PullRequestReviewEvent` on a `dependabot[bot]`/`renovate[bot]` PR                 |

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

Cloudflare Worker (tend-website)
  ├─ reads data/consumers.json via raw URL (KV-cached 1 h)
  ├─ /currently-tending: fans out to actions/runs per repo
  ├─ /activity:          fans out events-timeline + merged-PR search per bot
  ├─ /stats:             5 Search queries per bot, sums total_count
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
