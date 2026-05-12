// Cloudflare Worker that serves the tend website's live data streams.
//
// Three routes, all CORS-enabled JSON, each with its own edge-cache TTL
// matched to the freshness budget:
//
//   /currently-tending   30 s   in-progress tend-* workflow runs
//   /activity            5 min  recent things tend has done (PRs, reviews,
//                               comments, pushes, issue closes, dep approvals)
//   /stats               1 h    lifetime + this-week counters
//
// All three read the consumer list (`consumers.json`) from the repo, KV-
// cached for an hour, and fan out to GitHub. The edge cache coalesces
// concurrent misses — origin load is bounded by TTL, not viewer count.
//
// `/activity` is built from each bot's public event timeline
// (`GET /users/<bot>/events/public`) — one cheap REST call per bot, already
// discriminated by event type — plus one Search query per bot for merged
// PRs (the one "tend did this" milestone the bot's own event stream can't
// see, since the merge is usually performed by a human).
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

type ActivityKind =
  | "pr-opened" // tend opened a PR (CI fix, maintenance, workflow self-edit, …)
  | "pr-merged" // a tend-authored PR shipped
  | "pr-reviewed" // tend approved / requested changes on a PR
  | "pr-commented" // tend left comments on a PR (review bodies, inline, conversation)
  | "pr-commits" // tend pushed commits to a PR branch (review fixes, conflict resolution)
  | "issue-commented" // tend commented on an issue (triage, mention answer)
  | "issue-closed" // tend closed a resolved issue
  | "dep-approved"; // tend cleared a dependency bump (dependabot/renovate PR)

type ReviewVerdict = "approved" | "changes_requested";
type PrCategory = "ci-fix" | "issue-fix" | "workflow" | "maintenance" | "other";

interface ActivityDetail {
  count?: number; // pr-commented / issue-commented / pr-commits — how many
  verdict?: ReviewVerdict; // pr-reviewed
  category?: PrCategory; // pr-opened — inferred from the head-branch name
}

interface ActivityEvent {
  repo: string;
  kind: ActivityKind;
  title: string;
  url: string;
  at: string; // ISO; for collapsed kinds, the most recent constituent event
  detail?: ActivityDetail;
}

interface ActivityResponse {
  generated_at: string;
  events: ActivityEvent[];
}

// Slim view of a `GET /users/<bot>/events` item — every field optional
// because the payload shape varies by `type` and the events API serves
// trimmed-down webhook payloads.
interface GitHubEvent {
  type?: string;
  created_at?: string;
  repo?: { name?: string }; // "owner/name"
  payload?: {
    action?: string;
    pull_request?: {
      html_url?: string;
      title?: string;
      user?: { login?: string };
      head?: { ref?: string };
    };
    issue?: { html_url?: string; title?: string; pull_request?: unknown };
    review?: { state?: string };
    ref?: string;
    head?: string;
    size?: number;
    commits?: Array<{ sha?: string; message?: string }>;
  };
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
  closed_at?: string | null; // present (≈ merge time) for merged PRs
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
// /activity: cap the merged feed, pull one page (~100) of each bot's event
// timeline, and the ~10 most-recent merged PRs per bot. Together that covers
// roughly a week at current volume; the events API only serves the last 30
// days regardless, so the feed is inherently bounded.
const ACTIVITY_LIMIT = 40;
const EVENTS_PER_PAGE = 100;
const MERGED_PR_LIMIT = 10;
const DEPENDENCY_BOTS = new Set(["dependabot[bot]", "renovate[bot]"]);
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
  const consumers = await getConsumers(env);
  const bots = botNames(consumers);
  if (bots.length === 0) {
    return { generated_at: nowIso(), events: [] };
  }
  const consumerRepos = new Set(consumers.map((c) => c.repo));

  // Per bot: one page of its public event timeline (cheap REST), plus one
  // Search for its recently-merged PRs (the merge event's actor is usually a
  // human, so it isn't in the bot's own stream).
  const perBot = await Promise.all(
    bots.map(async (bot) => {
      const [events, merged] = await Promise.all([
        fetchBotEvents(bot, env.GITHUB_TOKEN),
        searchIssues(
          `author:${bot} is:pr is:merged`,
          env.GITHUB_TOKEN,
          MERGED_PR_LIMIT,
        ),
      ]);
      return { events, merged };
    }),
  );

  const fromEvents = eventsToActivity(
    perBot.flatMap((b) => b.events),
    consumerRepos,
  );
  const fromMerges: ActivityEvent[] = perBot
    .flatMap((b) => b.merged)
    .map((item) => ({
      repo: repoFromApiUrl(item.repository_url),
      kind: "pr-merged" as const,
      title: item.title,
      url: item.html_url,
      at: item.closed_at ?? item.updated_at, // closed_at ≈ merge time
    }))
    .filter((e) => consumerRepos.has(e.repo));

  // Dedup by kind+url (a PR legitimately recurs across kinds — opened, then
  // commented, then merged — those stay; only same-kind dups collapse).
  const byKey = new Map<string, ActivityEvent>();
  for (const e of [...fromEvents, ...fromMerges]) {
    const key = `${e.kind}|${e.url}`;
    const prev = byKey.get(key);
    if (!prev || e.at > prev.at) byKey.set(key, e);
  }
  const events = [...byKey.values()]
    .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
    .slice(0, ACTIVITY_LIMIT);
  return { generated_at: nowIso(), events };
}

async function fetchBotEvents(
  bot: string,
  token: string,
): Promise<GitHubEvent[]> {
  if (!isValidBotName(bot)) {
    console.error(`skipping malformed bot: ${bot}`);
    return [];
  }
  const url = `${GITHUB_API}/users/${bot}/events/public?per_page=${EVENTS_PER_PAGE}`;
  const resp = await fetchWithTimeout(url, { headers: githubHeaders(token) });
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      throw new Error(`events auth failure for ${bot}: ${resp.status}`);
    }
    console.error(`events fetch skipped for ${bot}: ${resp.status}`);
    return [];
  }
  const data = await resp.json();
  return Array.isArray(data) ? (data as GitHubEvent[]) : [];
}

// Map a flat list of GitHub events (any bots, any repos) to activity rows,
// keeping only events in consumer repos. Comments and pushes to the same
// PR/branch collapse into one row with a count.
function eventsToActivity(
  events: GitHubEvent[],
  consumerRepos: Set<string>,
): ActivityEvent[] {
  const out: ActivityEvent[] = [];
  // Collapse keys: PR url for comments, repo@branch for pushes.
  const prComments = new Map<string, ActivityEvent>();
  const issueComments = new Map<string, ActivityEvent>();
  const branchPushes = new Map<string, ActivityEvent>();

  const bump = (
    bucket: Map<string, ActivityEvent>,
    key: string,
    seed: ActivityEvent,
    add = 1,
  ) => {
    const existing = bucket.get(key);
    if (!existing) {
      seed.detail = { ...seed.detail, count: add };
      bucket.set(key, seed);
      return;
    }
    existing.detail = { ...existing.detail, count: (existing.detail?.count ?? 0) + add };
    if (seed.at > existing.at) {
      existing.at = seed.at;
      existing.url = seed.url;
      existing.title = seed.title;
    }
  };

  for (const e of events) {
    const repo = e.repo?.name;
    const at = e.created_at;
    const p = e.payload;
    if (!repo || !at || !p || !consumerRepos.has(repo)) continue;

    switch (e.type) {
      case "PullRequestEvent": {
        const pr = p.pull_request;
        if (p.action !== "opened" || !pr?.html_url) break;
        out.push({
          repo,
          kind: "pr-opened",
          title: pr.title ?? "",
          url: pr.html_url,
          at,
          detail: { category: prCategory(pr.head?.ref) },
        });
        break;
      }
      case "PullRequestReviewEvent": {
        const pr = p.pull_request;
        if (p.action !== "created" || !pr?.html_url) break;
        const state = p.review?.state;
        if (
          state === "approved" &&
          pr.user?.login &&
          DEPENDENCY_BOTS.has(pr.user.login)
        ) {
          out.push({
            repo,
            kind: "dep-approved",
            title: pr.title ?? "",
            url: pr.html_url,
            at,
          });
          break;
        }
        if (state === "approved" || state === "changes_requested") {
          out.push({
            repo,
            kind: "pr-reviewed",
            title: pr.title ?? "",
            url: pr.html_url,
            at,
            detail: { verdict: state },
          });
        } else {
          bump(prComments, pr.html_url, {
            repo,
            kind: "pr-commented",
            title: pr.title ?? "",
            url: pr.html_url,
            at,
          });
        }
        break;
      }
      case "PullRequestReviewCommentEvent": {
        const pr = p.pull_request;
        if (p.action !== "created" || !pr?.html_url) break;
        bump(prComments, pr.html_url, {
          repo,
          kind: "pr-commented",
          title: pr.title ?? "",
          url: pr.html_url,
          at,
        });
        break;
      }
      case "IssueCommentEvent": {
        const issue = p.issue;
        if (p.action !== "created" || !issue?.html_url) break;
        if (issue.pull_request) {
          bump(prComments, issue.html_url, {
            repo,
            kind: "pr-commented",
            title: issue.title ?? "",
            url: issue.html_url,
            at,
          });
        } else {
          bump(issueComments, issue.html_url, {
            repo,
            kind: "issue-commented",
            title: issue.title ?? "",
            url: issue.html_url,
            at,
          });
        }
        break;
      }
      case "IssuesEvent": {
        const issue = p.issue;
        if (p.action !== "closed" || !issue?.html_url) break;
        out.push({
          repo,
          kind: "issue-closed",
          title: issue.title ?? "",
          url: issue.html_url,
          at,
        });
        break;
      }
      case "PushEvent": {
        const branch = branchFromRef(p.ref);
        const size = p.size ?? 1;
        if (!branch || size < 1) break; // default branch, tag, or no-op push
        const head = p.head;
        const url = head
          ? `https://github.com/${repo}/commit/${head}`
          : `https://github.com/${repo}/commits/${branch}`;
        const headCommit =
          (p.commits ?? []).find((c) => c.sha === head) ??
          (p.commits ?? []).at(-1);
        const title = firstLine(headCommit?.message) || `pushed to ${branch}`;
        bump(
          branchPushes,
          `${repo}|${branch}`,
          { repo, kind: "pr-commits", title, url, at },
          size,
        );
        break;
      }
    }
  }

  for (const m of [prComments, issueComments, branchPushes]) {
    out.push(...m.values());
  }
  return out;
}

// Best-effort: tend's branch conventions are fix/ci-<run> (ci-fix),
// fix/issue-<n> and repro/issue-<n> (issue-fix), tend/update-workflows
// (workflow self-edit), and tend/<task> (other maintenance).
function prCategory(ref?: string): PrCategory {
  if (!ref) return "other";
  if (ref.startsWith("fix/ci")) return "ci-fix";
  if (ref.startsWith("fix/") || ref.startsWith("repro/")) return "issue-fix";
  if (ref.includes("update-workflows") || ref.includes("workflow")) return "workflow";
  if (ref.startsWith("tend/")) return "maintenance";
  return "other";
}

function branchFromRef(ref?: string): string | null {
  if (!ref?.startsWith("refs/heads/")) return null;
  const branch = ref.slice("refs/heads/".length);
  if (!branch || branch === "main" || branch === "master") return null;
  return branch;
}

function firstLine(s?: string): string {
  return (s?.split("\n", 1)[0] ?? "").trim();
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
  fetchBotEvents,
  eventsToActivity,
  prCategory,
  getConsumers,
  isConsumerArray,
  isValidRepo,
  isValidBotName,
};
