# tend-currently Worker

Cloudflare Worker that serves the "currently tending" data stream — a 30s-fresh
view of in-progress tend-* workflow runs across opt-in repos. See
[`../docs/website-data.md`](../docs/website-data.md) for the broader architecture
and [`../WEBSITE-live-data.md`](../WEBSITE-live-data.md) for the rate-limit
reasoning.

## Endpoint

```
GET https://tend-currently.<your-subdomain>.workers.dev/
```

Returns:

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

CORS allows `https://max-sixty.github.io` (override via `ALLOWED_ORIGIN` in
`wrangler.toml`). Browser cache TTL = 30s, matching the KV cache.

## One-time setup

```sh
npm install
npx wrangler login                                  # opens browser
npx wrangler kv:namespace create CACHE              # prints the id
#   → paste the id into wrangler.toml ([[kv_namespaces]] id)
npx wrangler secret put GITHUB_TOKEN                # paste a read-only PAT
npx wrangler deploy                                 # first deploy
```

The PAT needs `actions:read` + `metadata:read` on the repos listed in
`repos.json`. Public repos don't require additional permissions.

After first deploy, CI handles subsequent deploys via
[`../.github/workflows/worker-deploy.yaml`](../.github/workflows/worker-deploy.yaml).
That workflow needs `CLOUDFLARE_API_TOKEN` set as a repo secret — generate one
at <https://dash.cloudflare.com/profile/api-tokens> with the "Edit Workers"
template.

## Local development

```sh
npm run dev      # wrangler dev with hot reload at http://localhost:8787
npm test         # unit tests (vitest, no Worker runtime needed)
npm run typecheck
```

`wrangler dev` reads the same `wrangler.toml`. For the GitHub token locally,
either `wrangler secret put GITHUB_TOKEN` (puts it in CF only, doesn't run
locally) or set it via a `.dev.vars` file:

```
GITHUB_TOKEN=ghp_...
```

`.dev.vars` is gitignored.

## Cache strategy

- `CACHE` KV namespace, key `currently-tending:v1`, TTL 30s — the rendered
  response. One render serves all viewers within the window.
- Key `repos:v1`, TTL 1h — the repo list fetched from
  `raw.githubusercontent.com`. Decouples discovery updates from Worker deploys
  while keeping the dependency lightweight.

A cold cache miss costs N parallel GitHub API calls (one per repo). With N=20
and a 30s TTL, that's ~2 calls/sec worst case — well below the 5,000/hour limit
on the PAT.
