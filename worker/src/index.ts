// Cloudflare Worker that serves the tend website's live data streams.
//
// Two routes, both CORS-enabled JSON, each with its own freshness budget:
//
//   /currently-tending   30 s   in-progress tend-* workflow runs
//   /activity            5 min  recent PRs / issues / reviews / comments +
//                               lifetime counts, per primitive bucket
//
// Both read the consumer list (`consumers.json`) from the repo, KV-cached
// for an hour, and fan out to GitHub. Responses are stale-while-revalidate:
// a request is always answered from the colo cache (no waiting on the
// fanout) and, when the cached entry is past its budget, also kicks off a
// background refresh so the next request sees fresher data. Only a cold
// cache — a fresh deploy, or a quiet stretch long enough for the entry to
// be evicted — makes a viewer wait. A background refresh only fires on a
// request, so idle days still cost zero GitHub calls.
//
// `/activity` is one Search query per bucket per bot (`sort=updated`): the
// page yields both the recent items and the lifetime `total_count`; "this
// week" is counted off the page, so it saturates around one page (~100) per
// bot per bucket — fine for a headline number. The fanout is 4·N concurrent
// Search requests, under the 30/min cap up to ~7 bots.
//
// See ../README.md for architecture and the rate-limit reasoning behind
// the budgets.

interface Env {
  GITHUB_TOKEN: string;
  CACHE: KVNamespace;
  ALLOWED_ORIGIN: string;
  REPOS_URL: string;
}

interface Consumer {
  repo: string; // "owner/name"
  bot_name: string;
}

interface WorkflowRun {
  name?: string;
  run_started_at?: string;
  html_url?: string;
}

interface RunsResponse {
  workflow_runs?: WorkflowRun[];
}

interface CurrentlyTendingEntry {
  repo: string;
  workflow: string;
  started_at: string;
  run_url: string;
}

interface CurrentlyTendingResponse {
  generated_at: string;
  currently_tending: CurrentlyTendingEntry[];
}

// /activity: one bucket per primitive Search query, named off the query.
type ActivityBucketName = "prs" | "issues" | "reviews" | "comments";

interface RecentItem {
  repo: string; // "owner/name"
  title: string;
  url: string;
  at: string; // issue/PR updated_at, ISO
}

interface ActivityBucket {
  count: number; // lifetime — Search total_count, summed across bots
  count_this_week: number; // last 7 days; saturates ~one page per bot per bucket
  recent: RecentItem[]; // newest-first, merged across bots
}

type ActivityResponse = {
  generated_at: string;
} & Record<ActivityBucketName, ActivityBucket>;

interface SearchItem {
  html_url: string;
  title: string;
  updated_at: string;
  repository_url: string;
  number: number;
}

interface SearchResponse {
  total_count?: number;
  items?: SearchItem[];
}

// GitHub comment objects — fields we actually use. Same shape for inline
// PR review comments (`/pulls/{n}/comments`) and conversation comments
// (`/issues/{n}/comments`).
interface IssueCommentObject {
  user?: { login?: string } | null;
  html_url?: string;
  created_at?: string;
}

const REPOS_KEY = "repos:v1";
const REPOS_TTL_SECONDS = 3600;
const FETCH_TIMEOUT_MS = 10_000;
const WORKFLOW_PREFIX = "tend-";
// `actions/runs` sorts by created_at desc across ALL workflows in the
// repo, then we filter to tend-* client-side. 30 (GitHub's default) is
// cheap and avoids tend runs being pushed off by busier non-tend traffic.
const PER_PAGE_RUNS = 30;
// /activity: one Search page per bucket per bot. 100 is Search's max page
// and one request; we keep the newest RECENT_PER_BUCKET for the feed and
// count the rest of the page towards "this week".
const SEARCH_PAGE = 100;
const RECENT_PER_BUCKET = 10;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const GITHUB_API = "https://api.github.com";
const USER_AGENT = "tend-website-worker";

// tend's own bookkeeping issues — "Bot temporarily unavailable" outage
// trackers, the monthly review-runs / review-reviewers trackers, nightly
// drift-cleanup notes — carry these labels. The `issues` bucket excludes
// them so its count reflects issues filed about the repo, not tend's
// internal record-keeping. A repo without a label just matches nothing.
const BOOKKEEPING_LABELS = [
  "tend-outage",
  "review-runs-tracking",
  "review-reviewers-tracking",
  "nightly-cleanup",
];
const ISSUE_LABEL_FILTER = BOOKKEEPING_LABELS.map((l) => `-label:${l}`).join(" ");

// Why primitive buckets, not a job taxonomy: GitHub records mechanical facts
// (PR opened, review submitted, comment created), but tend's jobs (review /
// triage / ci-fix / nightly / weekly) don't map onto them cleanly — a PR on
// `fix/ci-*` vs `fix/issue-*` vs `tend/update-workflows` is the same event
// type, and a nightly survey that finds nothing leaves no trace. An earlier
// cut reverse-engineered a `kind` enum from branch names and still
// mislabelled things. The site renders the mechanical buckets directly; the
// "what's tend been up to" narrative is deferred to a Phase 2 LLM summary
// (see TODO.md).
//
// `q` for each /activity bucket — "the bot …":
const BUCKET_QUERIES: Record<ActivityBucketName, (bot: string) => string> = {
  prs: (b) => `author:${b} is:pr`, // …opened these PRs
  issues: (b) => `author:${b} is:issue ${ISSUE_LABEL_FILTER}`, // …opened these issues (minus its own bookkeeping)
  reviews: (b) => `reviewed-by:${b}`, // …reviewed these PRs (approve / request-changes / review comment)
  comments: (b) => `commenter:${b} -author:${b} -reviewed-by:${b}`, // …commented on these PRs/issues (not its own, not folded in from a review)
};

// Per-route freshness budgets, in seconds. `ok` applies to a good refresh;
// `fallback` (used when the refresh throws) is shorter so a transient
// outage clears quickly. Past its budget a cached entry is still served —
// see serveCached's stale-while-revalidate — until it's STALE_SERVE_FACTOR
// budgets old, at which point the colo cache drops it.
const TTL = {
  "currently-tending": { ok: 30, fallback: 5 },
  activity: { ok: 300, fallback: 30 },
} as const;

// Multiple of the freshness budget that a cached entry stays serveable
// before the colo cache evicts it. Bounds how stale a viewer can see; the
// larger it is, the longer a quiet site stays warm between refreshes.
const STALE_SERVE_FACTOR = 10;

// serveCached stamps each cached response with the instant (epoch ms) past
// which a hit should trigger a background refresh.
const STALE_AT_HEADER = "x-tend-stale-at";

// How far forward a stale-hit pushes its cached entry's stale-at before
// firing the background refresh. Concurrent stale-hits within this window
// read the bumped entry as fresh and skip starting their own refresh, so
// the colo runs one refresh per stale window instead of one per viewer.
// Comfortably above the FETCH_TIMEOUT_MS=10s worst-case refresh time; the
// real refresh's put overwrites the bumped entry as soon as it completes.
const REFRESH_GRACE_MS = 30_000;

// owner/name — alphanumerics + `_-.`, no leading `.`/`-`, no `..` anywhere,
// exactly one slash.
const REPO_PART = /^[A-Za-z0-9_][A-Za-z0-9._-]*$/;
function isValidRepo(repo: string): boolean {
  if (repo.includes("..")) return false;
  const parts = repo.split("/");
  return parts.length === 2 && parts.every((p) => REPO_PART.test(p));
}

// GitHub bot usernames are unconstrained enough that we validate before
// interpolating into a Search query. Letters/digits/`-`/`_`, max 39 chars
// (GitHub's own cap).
const BOT_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,38}$/;
function isValidBotName(name: string): boolean {
  return BOT_NAME.test(name);
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (request.method === "OPTIONS") {
      return corsPreflight(env);
    }
    if (request.method !== "GET") {
      return withCors(new Response("Method Not Allowed", { status: 405 }), env);
    }

    const url = new URL(request.url);
    switch (url.pathname) {
      case "/":
      case "/currently-tending":
        return serveCached(url, env, ctx, {
          cacheKeyPath: "/currently-tending",
          ttl: TTL["currently-tending"],
          refresh: () => refreshCurrentlyTending(env),
          empty: () => ({ generated_at: nowIso(), currently_tending: [] }),
        });
      case "/activity":
        return serveCached(url, env, ctx, {
          cacheKeyPath: "/activity",
          ttl: TTL.activity,
          refresh: () => refreshActivity(env),
          empty: emptyActivity,
        });
      default:
        return withCors(new Response("Not Found", { status: 404 }), env);
    }
  },
};

// ---------------------------------------------------------------------------
// Cache-and-serve

interface CacheOpts<T> {
  cacheKeyPath: string;
  ttl: { ok: number; fallback: number };
  refresh: () => Promise<T>;
  empty: () => T;
}

async function serveCached<T>(
  url: URL,
  env: Env,
  ctx: ExecutionContext,
  opts: CacheOpts<T>,
): Promise<Response> {
  // Normalize the cache key so query strings don't fork the cache. Using
  // the colo cache (not KV) for the response also dodges KV's 60s minimum
  // expirationTtl — currently-tending's budget is shorter than that.
  const cacheKey = new Request(`${url.origin}${opts.cacheKeyPath}`, {
    method: "GET",
  });
  const cached = await caches.default.match(cacheKey).catch(() => undefined);

  if (cached) {
    // Stale-while-revalidate: answer from cache immediately — never make a
    // viewer wait on the GitHub fanout. Past its freshness budget, kick
    // off a background refresh so the next request gets fresher data.
    // coalesceAndRefresh first pushes the entry's stale-at forward by
    // REFRESH_GRACE_MS so concurrent stale-hits within that window read
    // the entry as fresh and skip starting their own refresh — one refresh
    // per stale window per colo instead of one per viewer.
    if (isStale(cached.headers.get(STALE_AT_HEADER), Date.now())) {
      ctx.waitUntil(
        coalesceAndRefresh(cacheKey, cached, env, opts).catch((e) =>
          console.error(
            `background refresh failed for ${opts.cacheKeyPath}:`,
            e,
          ),
        ),
      );
    }
    return cached;
  }

  // Cold cache — a fresh deploy, or no traffic for STALE_SERVE_FACTOR
  // budgets. This request pays the fanout; everyone after it is served
  // from cache until the next cold start.
  return refreshAndCache(cacheKey, env, ctx, opts);
}

// Run the configured refresh and shape it into a Response stamped with
// its freshness budget. On any unexpected failure return the empty
// payload tagged with the shorter fallback budget so a transient outage
// clears quickly instead of wedging the cache.
async function freshResponse<T>(env: Env, opts: CacheOpts<T>): Promise<Response> {
  let fresh: T;
  let ttlSeconds = opts.ttl.ok;
  try {
    fresh = await opts.refresh();
  } catch (e) {
    console.error(`refresh failed for ${opts.cacheKeyPath}:`, e);
    fresh = opts.empty();
    ttlSeconds = opts.ttl.fallback;
  }
  return jsonResponse(fresh, env, ttlSeconds);
}

// Cold-cache path: the caller is waiting on this, so we return the fresh
// Response and let the cache put run via ctx.waitUntil.
async function refreshAndCache<T>(
  cacheKey: Request,
  env: Env,
  ctx: ExecutionContext,
  opts: CacheOpts<T>,
): Promise<Response> {
  const response = await freshResponse(env, opts);
  ctx.waitUntil(caches.default.put(cacheKey, response.clone()));
  return response;
}

// Stale-hit path: coalesce concurrent refreshes by bumping the cached
// entry's stale-at forward before running the refresh. Concurrent
// stale-hits arriving within REFRESH_GRACE_MS read the bumped entry as
// fresh and skip starting their own refresh. The puts are sequenced —
// bumped first, then the fresh result — so the fresh result always wins.
async function coalesceAndRefresh<T>(
  cacheKey: Request,
  cached: Response,
  env: Env,
  opts: CacheOpts<T>,
): Promise<void> {
  const bumped = new Response(cached.clone().body, {
    status: cached.status,
    headers: new Headers(cached.headers),
  });
  bumped.headers.set(STALE_AT_HEADER, String(Date.now() + REFRESH_GRACE_MS));
  await caches.default.put(cacheKey, bumped);
  await caches.default.put(cacheKey, await freshResponse(env, opts));
}

// A cached entry past this instant (epoch ms, from STALE_AT_HEADER) is
// still served but triggers a background refresh. A missing or garbled
// stamp counts as stale, so an entry written before this scheme — or any
// the cache mangles — gets refreshed promptly.
function isStale(staleAtHeader: string | null, nowMs: number): boolean {
  const staleAt = Number(staleAtHeader);
  return !Number.isFinite(staleAt) || nowMs >= staleAt;
}

// ---------------------------------------------------------------------------
// /currently-tending

async function refreshCurrentlyTending(env: Env): Promise<CurrentlyTendingResponse> {
  const consumers = await getConsumers(env);
  const perRepo = await Promise.all(
    consumers.map((c) => fetchRepoRuns(c.repo, env.GITHUB_TOKEN)),
  );
  const entries = perRepo.flat();
  entries.sort((a, b) => {
    if (a.started_at !== b.started_at) {
      return a.started_at < b.started_at ? 1 : -1;
    }
    return a.repo < b.repo ? -1 : 1;
  });
  return { generated_at: nowIso(), currently_tending: entries };
}

async function fetchRepoRuns(
  repo: string,
  token: string,
): Promise<CurrentlyTendingEntry[]> {
  if (!isValidRepo(repo)) {
    console.error(`skipping malformed repo: ${repo}`);
    return [];
  }
  const url =
    `${GITHUB_API}/repos/${repo}/actions/runs` +
    `?status=in_progress&per_page=${PER_PAGE_RUNS}`;
  const resp = await fetchWithTimeout(url, { headers: githubHeaders(token) });
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      throw new Error(`auth failure for ${repo}: ${resp.status}`);
    }
    console.error(`runs fetch skipped for ${repo}: ${resp.status}`);
    return [];
  }
  const data = (await resp.json()) as RunsResponse;
  return (data.workflow_runs ?? [])
    .filter(
      (
        run,
      ): run is WorkflowRun & {
        name: string;
        run_started_at: string;
        html_url: string;
      } =>
        typeof run.name === "string" &&
        run.name.startsWith(WORKFLOW_PREFIX) &&
        typeof run.run_started_at === "string" &&
        typeof run.html_url === "string",
    )
    .map((run) => ({
      repo,
      workflow: run.name,
      started_at: run.run_started_at,
      run_url: run.html_url,
    }));
}

// ---------------------------------------------------------------------------
// /activity

async function refreshActivity(env: Env): Promise<ActivityResponse> {
  const out = emptyActivity();
  const bots = botNames(await getConsumers(env));
  if (bots.length === 0) return out;
  const weekAgoMs = Date.now() - WEEK_MS;

  await Promise.all(
    (Object.keys(BUCKET_QUERIES) as ActivityBucketName[]).map(async (name) => {
      const pages = await Promise.all(
        bots.map(async (b) => ({
          bot: b,
          page: await searchIssues(BUCKET_QUERIES[name](b), env.GITHUB_TOKEN),
        })),
      );
      const items: Array<SearchItem & { bot: string }> = [];
      let count = 0;
      let countThisWeek = 0;
      for (const { bot, page } of pages) {
        count += page.total_count ?? 0;
        for (const it of page.items ?? []) {
          items.push({ ...it, bot });
          if (Date.parse(it.updated_at) >= weekAgoMs) countThisWeek++;
        }
      }
      items.sort((a, b) =>
        a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0,
      );
      const top = items.slice(0, RECENT_PER_BUCKET);
      const recent = await Promise.all(
        top.map((it) => toRecentItem(name, it, env.GITHUB_TOKEN)),
      );
      out[name] = { count, count_this_week: countThisWeek, recent };
    }),
  );
  return out;
}

// `prs`/`issues` rows link to the parent — that IS what the bot created.
// `reviews`/`comments` rows link to the bot's specific review/comment so
// clicking lands on tend's actual action, not the top of the thread.
// Follow-up failure (404, transient error, race where Search saw the action
// but the comment isn't queryable yet) falls back to the parent URL — better
// than dropping the row.
async function toRecentItem(
  bucket: ActivityBucketName,
  it: SearchItem & { bot: string },
  token: string,
): Promise<RecentItem> {
  const repo = repoFromApiUrl(it.repository_url);
  const base = {
    repo,
    title: it.title,
    at: it.updated_at,
  };
  if (bucket === "reviews") {
    const deep = await findBotReviewUrl(repo, it.number, it.bot, token);
    return { ...base, url: deep ?? it.html_url };
  }
  if (bucket === "comments") {
    const deep = await findBotCommentUrl(repo, it.number, it.bot, token);
    return { ...base, url: deep ?? it.html_url };
  }
  return { ...base, url: it.html_url };
}

// Latest inline review comment on a PR authored by `bot` — `created_at` desc.
// We deliberately don't anchor on the review summary (`#pullrequestreview-…`):
// tend's reviews are typically `COMMENTED` with an empty body wrapping inline
// comments, so the review anchor scrolls nowhere on the conversation page.
// `#discussion_r…` anchors land on the actual comment thread. Returns null if
// the fetch fails or the bot left no inline comments (caller falls back to
// the parent PR URL).
async function findBotReviewUrl(
  repo: string,
  n: number,
  bot: string,
  token: string,
): Promise<string | null> {
  const url = `${GITHUB_API}/repos/${repo}/pulls/${n}/comments?per_page=100`;
  try {
    const resp = await fetchWithTimeout(url, { headers: githubHeaders(token) });
    if (!resp.ok) {
      console.error(`review-comment lookup failed (${resp.status}): ${repo}#${n}`);
      return null;
    }
    const comments = (await resp.json()) as IssueCommentObject[];
    return latestByBot(
      comments,
      bot,
      (c) => c.created_at,
      (c) => c.html_url,
    );
  } catch (e) {
    console.error(`review-comment lookup error: ${repo}#${n}`, e);
    return null;
  }
}

// Latest issue/PR-conversation comment authored by `bot` — `created_at` desc.
// (Inline PR review comments live at a different endpoint; the comments
// bucket excludes `reviewed-by:<bot>`, so the bot's contribution is an
// issue-style comment.) Returns null on failure (caller falls back).
async function findBotCommentUrl(
  repo: string,
  n: number,
  bot: string,
  token: string,
): Promise<string | null> {
  const url = `${GITHUB_API}/repos/${repo}/issues/${n}/comments?per_page=100`;
  try {
    const resp = await fetchWithTimeout(url, { headers: githubHeaders(token) });
    if (!resp.ok) {
      console.error(`comment lookup failed (${resp.status}): ${repo}#${n}`);
      return null;
    }
    const comments = (await resp.json()) as IssueCommentObject[];
    return latestByBot(
      comments,
      bot,
      (c) => c.created_at,
      (c) => c.html_url,
    );
  } catch (e) {
    console.error(`comment lookup error: ${repo}#${n}`, e);
    return null;
  }
}

function latestByBot<T extends { user?: { login?: string } | null }>(
  entries: T[],
  bot: string,
  ts: (e: T) => string | undefined,
  href: (e: T) => string | undefined,
): string | null {
  let bestTs = "";
  let bestUrl: string | null = null;
  for (const e of entries) {
    if (e.user?.login !== bot) continue;
    const t = ts(e);
    const h = href(e);
    if (typeof t !== "string" || typeof h !== "string") continue;
    if (t > bestTs) {
      bestTs = t;
      bestUrl = h;
    }
  }
  return bestUrl;
}

function emptyActivity(): ActivityResponse {
  return {
    generated_at: nowIso(),
    prs: { count: 0, count_this_week: 0, recent: [] },
    issues: { count: 0, count_this_week: 0, recent: [] },
    reviews: { count: 0, count_this_week: 0, recent: [] },
    comments: { count: 0, count_this_week: 0, recent: [] },
  };
}

function repoFromApiUrl(repositoryUrl: string): string {
  // https://api.github.com/repos/owner/name -> owner/name
  const i = repositoryUrl.indexOf("/repos/");
  return i === -1 ? "" : repositoryUrl.slice(i + "/repos/".length);
}

// ---------------------------------------------------------------------------
// Search API

// One Search page, newest-first — the page yields both `items` (recent) and
// `total_count` (lifetime). 401/403 throws (sinks the refresh → short fallback
// TTL); 422/429/other degrade to `{}` so one bad bucket doesn't sink the rest.
async function searchIssues(query: string, token: string): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q: query,
    per_page: String(SEARCH_PAGE),
    sort: "updated",
    order: "desc",
  });
  const resp = await fetchWithTimeout(`${GITHUB_API}/search/issues?${params}`, {
    headers: githubHeaders(token),
  });
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      throw new Error(`search auth failure: ${resp.status}`);
    }
    console.error(`search failed (${resp.status}): ${query}`);
    return {};
  }
  return (await resp.json()) as SearchResponse;
}

// ---------------------------------------------------------------------------
// Consumers

async function getConsumers(env: Env): Promise<Consumer[]> {
  const cached = await env.CACHE.get<Consumer[]>(REPOS_KEY, "json").catch(() => null);
  if (cached) return cached;

  const resp = await fetchWithTimeout(env.REPOS_URL, {
    cf: { cacheTtl: REPOS_TTL_SECONDS },
  });
  if (!resp.ok) {
    throw new Error(`consumers.json fetch failed: ${resp.status}`);
  }
  const raw = await resp.json();
  if (!isConsumerArray(raw)) {
    throw new Error("consumers.json failed shape validation");
  }
  await env.CACHE.put(REPOS_KEY, JSON.stringify(raw), {
    expirationTtl: REPOS_TTL_SECONDS,
  }).catch((e) => console.error("repos KV put failed:", e));
  return raw;
}

function isConsumerArray(v: unknown): v is Consumer[] {
  return (
    Array.isArray(v) &&
    v.every(
      (e) =>
        typeof e === "object" &&
        e !== null &&
        typeof (e as { repo: unknown }).repo === "string" &&
        isValidRepo((e as { repo: string }).repo) &&
        typeof (e as { bot_name: unknown }).bot_name === "string" &&
        isValidBotName((e as { bot_name: string }).bot_name),
    )
  );
}

function botNames(consumers: Consumer[]): string[] {
  return Array.from(new Set(consumers.map((c) => c.bot_name))).sort();
}

// ---------------------------------------------------------------------------
// HTTP

async function fetchWithTimeout(input: string, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function githubHeaders(token: string): HeadersInit {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "User-Agent": USER_AGENT,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function withCors(resp: Response, env: Env): Response {
  resp.headers.set("Access-Control-Allow-Origin", env.ALLOWED_ORIGIN);
  return resp;
}

function jsonResponse(data: unknown, env: Env, ttlSeconds: number): Response {
  const staleAtMs = Date.now() + ttlSeconds * 1000;
  return withCors(
    new Response(JSON.stringify(data), {
      headers: {
        "Content-Type": "application/json",
        // Browsers revalidate after the freshness budget (`max-age`); the
        // colo cache, a shared cache, keeps the entry for STALE_SERVE_FACTOR
        // budgets (`s-maxage`) so it stays warm for stale-while-revalidate.
        // Assumes Cloudflare's zone cache isn't also storing this route — it
        // isn't on a vanilla Workers custom domain, but adding a Cache Rule
        // here would honor `s-maxage` and break SWR by shadowing the Worker.
        "Cache-Control":
          `public, max-age=${ttlSeconds}, ` +
          `s-maxage=${ttlSeconds * STALE_SERVE_FACTOR}`,
        [STALE_AT_HEADER]: String(staleAtMs),
      },
    }),
    env,
  );
}

function corsPreflight(env: Env): Response {
  return withCors(
    new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",
      },
    }),
    env,
  );
}

// Exported for unit tests.
export const __test = {
  refreshCurrentlyTending,
  refreshActivity,
  fetchRepoRuns,
  getConsumers,
  isConsumerArray,
  isValidRepo,
  isValidBotName,
  findBotReviewUrl,
  findBotCommentUrl,
  isStale,
};
