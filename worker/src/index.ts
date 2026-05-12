// Cloudflare Worker that serves the tend website's live data streams.
//
// Three routes, all CORS-enabled JSON, each with its own edge-cache TTL
// matched to the freshness budget:
//
//   /currently-tending   30 s   in-progress tend-* workflow runs
//   /activity            5 min  recent PR reviews / triage / CI-fix events
//   /stats               1 h    lifetime + this-week counters
//
// All three read the consumer list (`consumers.json`) from the repo, KV-
// cached for an hour, and fan out to GitHub. The edge cache coalesces
// concurrent misses — origin load is bounded by TTL, not viewer count.
//
// See docs/website-data.md for architecture and the rate-limit reasoning
// behind the TTLs.

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

type ActivityKind = "review" | "triage" | "ci-fix";

interface ActivityEvent {
  repo: string;
  kind: ActivityKind;
  title: string;
  url: string;
  at: string;
}

interface ActivityResponse {
  generated_at: string;
  events: ActivityEvent[];
}

interface StatsResponse {
  generated_at: string;
  reviews_total: number;
  reviews_this_week: number;
  ci_fixes_total: number;
  ci_fixes_this_week: number;
  triage_comments_total: number;
}

interface SearchItem {
  html_url: string;
  title: string;
  updated_at: string;
  repository_url: string;
}

interface SearchResponse {
  total_count?: number;
  items?: SearchItem[];
}

const REPOS_KEY = "repos:v1";
const REPOS_TTL_SECONDS = 3600;
const FETCH_TIMEOUT_MS = 10_000;
const WORKFLOW_PREFIX = "tend-";
// `actions/runs` sorts by created_at desc across ALL workflows in the
// repo, then we filter to tend-* client-side. 30 (GitHub's default) is
// cheap and avoids tend runs being pushed off by busier non-tend traffic.
const PER_PAGE_RUNS = 30;
const ACTIVITY_LIMIT = 10;
const GITHUB_API = "https://api.github.com";
const USER_AGENT = "tend-website-worker";

// Per-route TTLs. The fallback TTL (used when refresh throws) is shorter
// so a transient outage clears quickly.
const TTL = {
  "currently-tending": { ok: 30, fallback: 5 },
  activity: { ok: 300, fallback: 30 },
  stats: { ok: 3600, fallback: 60 },
} as const;

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
          empty: () => ({ generated_at: nowIso(), events: [] }),
        });
      case "/stats":
        return serveCached(url, env, ctx, {
          cacheKeyPath: "/stats",
          ttl: TTL.stats,
          refresh: () => refreshStats(env),
          empty: () => emptyStats(),
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
  // Normalize the cache key so query strings don't fork the cache. The
  // HTTP edge cache coalesces concurrent misses, which bounds origin
  // fanout AND dodges KV's 60s minimum expirationTtl.
  const cacheKey = new Request(`${url.origin}${opts.cacheKeyPath}`, {
    method: "GET",
  });
  const cached = await caches.default.match(cacheKey).catch(() => undefined);
  if (cached) {
    return cached;
  }

  // On any unexpected failure return the empty payload (with a short TTL)
  // so a transient outage doesn't break the page or wedge the cache.
  let fresh: T;
  let isFallback = false;
  try {
    fresh = await opts.refresh();
  } catch (e) {
    console.error(`refresh failed for ${opts.cacheKeyPath}:`, e);
    fresh = opts.empty();
    isFallback = true;
  }

  const ttl = isFallback ? opts.ttl.fallback : opts.ttl.ok;
  const response = jsonResponse(fresh, env, ttl);
  ctx.waitUntil(caches.default.put(cacheKey, response.clone()));
  return response;
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
  const bots = botNames(await getConsumers(env));
  if (bots.length === 0) {
    return { generated_at: nowIso(), events: [] };
  }
  // 3 queries × N bots, deduped by URL. First kind seen wins (matches the
  // pre-Worker Python fetcher's behavior so the UI doesn't shift on
  // cutover.) Order of kinds: ci-fix → review → triage.
  const kinds: Array<{ kind: ActivityKind; q: (bot: string) => string }> = [
    { kind: "ci-fix", q: (b) => `author:${b} is:pr` },
    { kind: "review", q: (b) => `commenter:${b} is:pr -author:${b}` },
    { kind: "triage", q: (b) => `commenter:${b} is:issue` },
  ];

  type Batch = { kind: ActivityKind; items: SearchItem[] };
  const batches: Batch[] = await Promise.all(
    kinds.flatMap(({ kind, q }) =>
      bots.map(async (bot) => ({
        kind,
        items: await searchIssues(q(bot), env.GITHUB_TOKEN, ACTIVITY_LIMIT),
      })),
    ),
  );

  // Walk batches in declared order to preserve "first kind seen wins".
  const seen = new Set<string>();
  const events: ActivityEvent[] = [];
  for (const { kind, items } of batches) {
    for (const item of items) {
      if (seen.has(item.html_url)) continue;
      seen.add(item.html_url);
      events.push({
        repo: repoFromApiUrl(item.repository_url),
        kind,
        title: item.title,
        url: item.html_url,
        at: item.updated_at,
      });
    }
  }
  events.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  return { generated_at: nowIso(), events: events.slice(0, ACTIVITY_LIMIT) };
}

function repoFromApiUrl(repositoryUrl: string): string {
  // https://api.github.com/repos/owner/name -> owner/name
  const i = repositoryUrl.indexOf("/repos/");
  return i === -1 ? "" : repositoryUrl.slice(i + "/repos/".length);
}

// ---------------------------------------------------------------------------
// /stats

async function refreshStats(env: Env): Promise<StatsResponse> {
  const bots = botNames(await getConsumers(env));
  if (bots.length === 0) {
    return emptyStats();
  }
  const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);

  // Each stat is summed across bots via Search's `total_count` (no
  // pagination needed). Five stats × N bots queries per refresh — cheap
  // because the TTL is an hour.
  const counters: Record<keyof Omit<StatsResponse, "generated_at">, (b: string) => string> = {
    reviews_total: (b) => `commenter:${b} is:pr -author:${b}`,
    reviews_this_week: (b) => `commenter:${b} is:pr -author:${b} updated:>=${weekAgo}`,
    ci_fixes_total: (b) => `author:${b} is:pr`,
    ci_fixes_this_week: (b) => `author:${b} is:pr updated:>=${weekAgo}`,
    triage_comments_total: (b) => `commenter:${b} is:issue`,
  };

  const keys = Object.keys(counters) as Array<keyof typeof counters>;
  const totals = await Promise.all(
    keys.map(async (key) => {
      const perBot = await Promise.all(
        bots.map((b) => searchCount(counters[key](b), env.GITHUB_TOKEN)),
      );
      return [key, perBot.reduce((a, b) => a + b, 0)] as const;
    }),
  );

  const out: StatsResponse = { generated_at: nowIso(), ...emptyCounts() };
  for (const [k, v] of totals) {
    out[k] = v;
  }
  return out;
}

function emptyStats(): StatsResponse {
  return { generated_at: nowIso(), ...emptyCounts() };
}

function emptyCounts() {
  return {
    reviews_total: 0,
    reviews_this_week: 0,
    ci_fixes_total: 0,
    ci_fixes_this_week: 0,
    triage_comments_total: 0,
  };
}

// ---------------------------------------------------------------------------
// Search API

async function searchIssues(
  query: string,
  token: string,
  perPage: number,
): Promise<SearchItem[]> {
  const data = await searchRaw(query, token, perPage);
  return data.items ?? [];
}

async function searchCount(query: string, token: string): Promise<number> {
  const data = await searchRaw(query, token, 1);
  return data.total_count ?? 0;
}

async function searchRaw(
  query: string,
  token: string,
  perPage: number,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, per_page: String(perPage) });
  if (perPage > 1) {
    params.set("sort", "updated");
    params.set("order", "desc");
  }
  const resp = await fetchWithTimeout(`${GITHUB_API}/search/issues?${params}`, {
    headers: githubHeaders(token),
  });
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      throw new Error(`search auth failure: ${resp.status}`);
    }
    // Other failures (incl. 422 — malformed query, 429 — rate-limited)
    // degrade to an empty result for this query so a single bad bot name
    // doesn't sink the whole refresh.
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
  return new Response(JSON.stringify(data), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": `public, max-age=${ttlSeconds}`,
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
    },
  });
}

function corsPreflight(env: Env): Response {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "*",
      "Access-Control-Max-Age": "86400",
    },
  });
}

// Exported for unit tests.
export const __test = {
  refreshCurrentlyTending,
  refreshActivity,
  refreshStats,
  fetchRepoRuns,
  getConsumers,
  isConsumerArray,
  isValidRepo,
  isValidBotName,
};
