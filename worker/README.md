# tend-website Worker

Cloudflare Worker that serves the data the tend marketing site renders —
`/currently-tending` (in-progress tend-* runs) and `/activity` (recent PRs /
issues / comments + lifetime counts) — each at its own route with its own
freshness budget. See [`../docs/website-data.md`](../docs/website-data.md)
for the route table, shapes, and the rate-limit reasoning.

## Endpoint

```
GET https://api.tend-src.com/{currently-tending|activity}
```

Example (`/currently-tending`) returns:

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

`Access-Control-Allow-Origin: *` (public read-only data).

## How it works

The Worker reads `data/consumers.json` from `main` via
`raw.githubusercontent.com` (KV-cached for 1 h) and fans out parallel
authenticated calls to GitHub:

- `/currently-tending` — one `GET /repos/{r}/actions/runs?status=in_progress`
  per consumer, filtered to `tend-*` workflows.
- `/activity` — one Search query per primitive bucket (`prs` / `issues` /
  `comments`) per bot.

Responses are served **stale-while-revalidate** from the colo cache
(`caches.default`). A request is always answered from cache — no waiting on
the GitHub fanout. When the cached entry is past its freshness budget
(`/currently-tending` 30 s, `/activity` 5 min), the hit also kicks off a
background refresh via `ctx.waitUntil` so the next viewer sees fresher data.
Only a cold cache — a fresh deploy, or a quiet stretch longer than ten
freshness budgets — makes one request synchronously refresh. Fallback
responses (when refresh throws) are cached with a short fallback budget so a
transient outage clears fast.

Origin load is bounded by the freshness budget, not viewer count. The
authenticated PAT's 5,000 req/hour and 30 Search req/min ceilings leave
plenty of headroom for the documented fanout shapes.

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
npm run dev      # wrangler dev with hot reload at http://localhost:8787
npm test         # unit tests (vitest, no Worker runtime needed)
npm run typecheck
```

`wrangler dev` reads the same `wrangler.toml`. For the GitHub token locally,
either `wrangler secret put GITHUB_TOKEN` (puts it in CF only, not local) or
set it via a `.dev.vars` file:

```
GITHUB_TOKEN=ghp_...
```

`.dev.vars` is gitignored.

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

A cold cache miss costs the route's full fanout (N actions/runs calls for
`/currently-tending`, 3·N Search calls for `/activity`). The freshness
budget bounds how often that happens: at most one cold refresh per budget
per colo, under any traffic level.
