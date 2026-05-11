# Tend website — live-data architecture

Research/design for the three "live data" features in `WEBSITE.md` §"Live data
(post-MVP)": **currently tending** indicator, **recent activity** feed, and
**stat** counters. Assumes the list of tend-running repos is a given input
(`repos.json`).

## 1. Rate-limit reality

All numbers below are from current GitHub docs (May 2026); citations at the
end of this section.

| Surface | Unauthenticated | Authenticated (PAT / OAuth user) | GitHub App install | `GITHUB_TOKEN` in Actions |
| --- | --- | --- | --- | --- |
| **REST core** | 60 / hour / IP | 5,000 / hour / token | 5,000 / hour / install (scales to 12.5k) | 1,000 / hour / repo |
| **Search API** | 10 / minute / IP | 30 / minute / token (9/min for code search) | 30 / minute / install | 30 / minute / repo |
| **GraphQL** | not supported | 5,000 points / hour / user | 5,000 points / hour / install | 1,000 points / hour / repo |

Secondary limits that bite in practice: **100 concurrent requests max**, **900
points/min per REST endpoint**, **2,000 points/min for GraphQL**, plus a
content-creation cap that doesn't apply to read-only use.

### Translating to viewer-pageload cost

Let N = number of tend-running repos in `repos.json` (assume **N ≈ 20** as a
plausible mid-term ceiling for planning).

| Feature | Calls per pageload | Mechanism |
| --- | --- | --- |
| Currently tending (poll every 30s) | N per poll = **20**; ~2,400 / hour / viewer | `GET /repos/{o}/{r}/actions/runs?status=in_progress&per_page=5` — one per repo. No search-API fan-in available for cross-repo run filtering. |
| Recent activity feed | **1–2** (one Search query, optional one detail call) | `GET /search/issues?q=commenter:tend-agent+is:pr-review-comment` |
| Stats | **0** if served from static JSON; otherwise 3–5 search queries | Same Search endpoint with different `q=` |

### Where the unauthenticated browser approach breaks

- **Currently tending**: breaks for **the first viewer on first poll**.
  60/hour ÷ 20 repos = 3 polls/hour of headroom; client polls every 30s, so a
  single tab burns the IP quota in under a minute. Doomed.
- **Recent activity**: Search at 10/min/IP supports ~10 viewers/min from a
  given egress IP. Works for a personal site, falls apart on any HN spike,
  and is shared across all viewers behind a corporate NAT.
- **Stats**: works if cached client-side (e.g. localStorage with 24h TTL),
  but a static JSON file is strictly cheaper.

### Citations

- REST primary limits — [docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- Search-specific limits — [docs.github.com/en/rest/search/search](https://docs.github.com/en/rest/search/search)
- GraphQL points — [docs.github.com/en/graphql/overview/resource-limitations](https://docs.github.com/en/graphql/overview/resource-limitations)
- Workflow-runs endpoint — [docs.github.com/en/rest/actions/workflow-runs](https://docs.github.com/en/rest/actions/workflow-runs)

## 2. Architecture options

| Option | Freshness ceiling | Cost @ 100 pv/day | Cost @ 10k pv/day | Complexity | Ops burden | Privacy |
| --- | --- | --- | --- | --- | --- | --- |
| **A. Browser-direct, unauth** | n/a — broken at any traffic | $0 | $0 (but unusable) | low | none | none (read-only public) |
| **B. Browser-direct, user PAT** | seconds | $0 | $0 | low | low | user gives us their token; bad UX |
| **C. Browser OAuth (web/device)** | seconds | $0 | $0 | medium | OAuth callback / app maintenance | user logs in to view a public site (weird) |
| **D. Nightly static JSON via Action** | **24 h** | $0 | $0 | low | one workflow | best — no runtime data flow |
| **E. Cloudflare Worker + KV/Cache, tend PAT** | seconds–minutes (TTL'd) | $0 (free tier) | $0–5 (free tier covers 100k req/day) | medium | one Worker, one secret | tend's PAT held by us |
| **F. Vercel / Netlify edge function** | same as E | $0 free tier | $0–20 | medium | platform lock-in | same |
| **G. Always-on box (Fly/Railway) polling on schedule** | seconds (push-style) | $5–10 | $5–10 | high | a service to keep alive | same |
| **H. Webhooks from consumer repos → our endpoint** | seconds | needs E or G to receive | same | high (per-consumer opt-in) | adoption gating | each consumer trusts us with webhook payloads |
| **I. tend as a GitHub App** | seconds (firehose) | infra cost as E/G | same | very high (re-platform the bot) | App listing, install flow | install-scoped — actually *better* than PAT-per-bot |

Notes on the rows:

- **A** is listed only to rule it out. The 60/hr/IP cap means even one
  enthusiastic viewer kills it. Public unauth proxies (jsDelivr, etc.) don't
  exist for arbitrary GitHub API paths — there's no trustworthy public mirror.
- **B/C** introduce a login wall to view a public marketing page. Reject.
- **D** is the cheapest possible thing and is what `WEBSITE.md` already
  sketches under §"Live data". It scales linearly with repos, not viewers.
- **E** is the right shape for sub-minute freshness. Cloudflare Workers free
  tier is 100k req/day, KV is 100k reads/day free. One Worker fronts
  `api.github.com`, caches by URL+TTL, serves CORS-allowed JSON to the static
  GitHub Pages site. The PAT lives in `wrangler secret`.
- **H/I** are interesting but not for MVP. A GitHub App is the right
  long-term home for tend's identity (per-install token, automatic listing,
  webhook firehose), but switching the bot's auth model is its own project
  and is orthogonal to the website.

## 3. Recommendation, per use case

The freshness budgets in the brief map cleanly to three different mechanisms.
**Don't try to unify them** — picking the cheapest tool that meets each
budget keeps the total bill near zero.

### Stats — **static JSON, nightly Action** (option D)

Already in `WEBSITE.md` phase 3 (§"GitHub data fetcher"). Daily freshness is
fine for "total reviews" and "this week" counts; the numbers don't move
meaningfully inside a day. Zero runtime cost, zero new infra, zero secrets
beyond a `GITHUB_TOKEN` scoped to the workflow.

### Recent activity feed — **static JSON, nightly Action** (option D)

The brief allows 5–15 min freshness, but a nightly commit is enough for the
"recent activity" affordance on a marketing page — the goal is
"yes, tend is active" not "real-time event stream." Use the same workflow
that builds stats; write `static/data/activity.json` alongside
`stats.json`. **If** post-launch we want sub-hour freshness, bump the cron
to every 15 min — still cheap, still no new infra. Promote to option E only
if 15-min cron commits look bad in the repo history.

### Currently tending — **Cloudflare Worker proxy with 30s cache** (option E), behind a graceful fallback

This is the only use case that genuinely can't be solved with static JSON.
The Worker:

1. On request, looks up cached JSON in Workers KV (key: `tending:v1`, TTL 30s).
2. On miss, fans out N parallel `GET /repos/.../actions/runs?status=in_progress&per_page=5`
   calls in `Promise.all`, authenticated with the tend PAT held as a Worker
   secret. With N=20 and a 30s TTL, this is **~2 GitHub calls/sec
   worst-case**, well under all primary and secondary caps regardless of
   viewer count.
3. Returns a compact JSON: `[{repo, workflow, started_at}, ...]`.
4. Sets `Cache-Control: public, max-age=30` so browsers and Cloudflare's
   edge cache do the right thing.

The client polls the Worker, not GitHub. CORS is set to the Pages origin.
**Fallback**: if the Worker is down or returns nothing, the UI shows
"last action 4m ago" sourced from the same nightly `activity.json` — the
indicator never breaks the page.

### Summary

| Use case | Mechanism | Freshness | Where data lives |
| --- | --- | --- | --- |
| Stats | Nightly Action → static JSON | 24 h | `/data/stats.json` on Pages |
| Activity feed | Nightly Action → static JSON | 24 h (15 min if needed) | `/data/activity.json` on Pages |
| Currently tending | Cloudflare Worker, 30 s KV cache | 30 s | Worker, fronted by Pages |

This is "mostly D, with one E" — keeps the website on GitHub Pages (option D
needs nothing else), and adds **one** small Worker only for the one feature
that actually requires it. The Worker is independent infra: if it breaks,
the rest of the site is unaffected.

## 4. Hosting implications

GitHub Pages serves static only; static + a separate Worker origin is the
combination that keeps the rest of `WEBSITE.md` (Zola → Pages from
`website` branch) intact. The static Pages site calls the Worker via CORS;
the Worker is on a `tend.workers.dev` subdomain (or attached to a custom
sub-subdomain like `api.tend.dev` if we own the apex).

If we end up wanting **more** dynamic features later (search, write
operations, per-viewer state), it would be worth re-evaluating moving the
whole site to Cloudflare Pages so the dynamic and static pieces share a
deploy. Not necessary for MVP.

## 5. Open questions for the user

- **Token ownership.** The Worker needs a PAT. Options: (a) a fine-grained
  PAT under `tend-agent` (the existing bot account) with read-only
  `actions:read` and `metadata:read` scope across the opt-in repos;
  (b) a PAT under your personal account scoped the same way; (c) a
  dedicated `tend-website` machine user. (a) is simplest, reuses an account
  already provisioned per consumer. (c) keeps blast radius separate from
  the bot's write-capable PAT — worth doing if the website's PAT will sit
  in Cloudflare and tend's bot PAT sits in Actions. **Recommended: (c).**
- **Hosting target.** Static stays on GitHub Pages. The Worker means signing
  up for Cloudflare (free) and registering the apex (`tend.dev` already
  open in `WEBSITE.md`). Confirm you're OK adding Cloudflare to the stack
  for just the "currently tending" feature; the alternative is dropping
  that feature to a 15-minute static refresh and going Cloudflare-free.
- **Opt-in from consumer repos.** The "currently tending" Worker needs to
  read `actions/runs` on each consumer repo. For public repos this is
  free-read; no opt-in needed. If we ever want to include **private** repos
  running tend, we need explicit opt-in plus a token with access — defer
  that until someone asks.
- **GitHub App migration timing.** Not blocking this design, but flag it:
  if tend moves to a GitHub App for its bot identity, the website Worker
  can switch to an installation token, and the activity feed could be fed
  by webhook events instead of polling. Worth keeping the Worker's GitHub
  client behind a small interface so the swap is local.
- **What "currently tending" returns when nothing is running.** Three
  options: hide the strip, show "idle" pill, or show "last action N minutes
  ago" from the activity data. `WEBSITE.md` already commits to the third;
  reconfirm before we wire the fallback.
