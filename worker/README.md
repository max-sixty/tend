# tend-website Worker

Cloudflare Worker that serves the data the tend marketing site renders —
`/currently-tending` (in-progress tend-* runs) and `/activity` (recent PRs /
issues / reviews / comments + lifetime counts) — each at its own route with its
own edge TTL. See [`../docs/website-data.md`](../docs/website-data.md) for the
route table, shapes, and the rate-limit reasoning.

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

`Access-Control-Allow-Origin: *` (public read-only data). Browser and
Cloudflare edge cache both honor `Cache-Control: public, max-age=30`.

## How it works

The Worker reads `data/consumers.json` from `main` via
`raw.githubusercontent.com` (KV-cached for 1 h), fans out parallel
`GET /repos/{r}/actions/runs?status=in_progress&per_page=30` calls
authenticated with a read-only PAT, filters to `tend-*` workflows, and
returns the compact JSON above. The rendered response is edge-cached
(`caches.default`) for 30 s, which both bounds GitHub fanout and dedupes
concurrent cache misses at the edge. Fallback responses (when refresh
fails) are cached for 5 s so a transient outage clears fast.

With N=5 consumers and a 30 s TTL, that's at most ~10 GitHub API calls/min
regardless of viewer count — well below the 5,000/hour limit on the PAT.

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

- `caches.default` (Cloudflare HTTP edge cache), keyed by the normalized
  request URL, TTL = `Cache-Control: max-age` — the rendered response.
  Edge dedupes concurrent misses, so a viral spike fans out at most once
  per 30 s per edge node.
- `CACHE` KV namespace, key `repos:v1`, TTL 1 h — the `consumers.json`
  content. Decouples `running-tend`'s weekly refresh from Worker deploys.
  KV is appropriate here because the 1 h TTL is above KV's 60 s minimum
  and we want cross-isolate sharing.

A cold cache miss costs N parallel GitHub API calls (one per consumer repo).
The 30 s TTL keeps the worst case to ~2 GitHub calls/sec for any traffic
level.
