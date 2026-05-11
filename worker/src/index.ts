// Cloudflare Worker that serves the "currently tending" data stream.
//
// Reads in-progress tend-* workflow runs across opt-in repos, edge-caches the
// rendered response for 30s, and returns CORS-enabled JSON to the tend
// marketing site.
//
// See docs/website-data.md for architecture; WEBSITE-live-data.md §3 for
// the rate-limit reasoning behind the 30s TTL + fanout pattern.

interface Env {
  GITHUB_TOKEN: string;
  CACHE: KVNamespace;
  ALLOWED_ORIGIN: string;
  REPOS_URL: string;
}

interface Consumer {
  repo: string; // "owner/name"
  bot_name: string; // unused by the Worker but kept for parity with consumers.json
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

const REPOS_KEY = "repos:v1";
const CACHE_TTL_SECONDS = 30;
const FALLBACK_TTL_SECONDS = 5; // shorter so a transient outage clears fast
const REPOS_TTL_SECONDS = 3600;
const FETCH_TIMEOUT_MS = 10_000;
const WORKFLOW_PREFIX = "tend-";
// GitHub's `actions/runs` endpoint sorts by created_at desc across ALL
// workflows in the repo, then we filter to tend-* client-side. A small
// page size risks the tend runs being pushed off by a flurry of non-tend
// runs in busy repos; 30 (GitHub's default) is cheap and removes the risk.
const PER_PAGE = 30;
const GITHUB_API = "https://api.github.com";
// owner/name — alphanumerics + `_-.`, no leading `.`/`-`, no `..` anywhere,
// exactly one slash. Stricter than GitHub itself; the cost of false rejection
// is a single repo missing from currently_tending until the bad entry is
// fixed in consumers.json.
const REPO_PART = /^[A-Za-z0-9_][A-Za-z0-9._-]*$/;
function isValidRepo(repo: string): boolean {
  if (repo.includes("..")) return false;
  const parts = repo.split("/");
  return parts.length === 2 && parts.every((p) => REPO_PART.test(p));
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
    if (url.pathname !== "/" && url.pathname !== "/currently-tending") {
      return withCors(new Response("Not Found", { status: 404 }), env);
    }

    // Edge-cache key — normalize the request URL so query strings don't fork
    // the cache. The HTTP cache coalesces concurrent misses at the edge,
    // which both bounds GitHub fanout and dodges the KV 60s expirationTtl
    // minimum (KV silently rejects sub-60s TTLs).
    const cacheKey = new Request(`${url.origin}/currently-tending`, {
      method: "GET",
    });
    const cached = await caches.default.match(cacheKey).catch(() => undefined);
    if (cached) {
      return cached;
    }

    // The UI fallback contract says the indicator never breaks the page —
    // on any unexpected failure, return an empty currently_tending so the
    // UI can fall back to activity.json's "last action N min ago".
    let fresh: CurrentlyTendingResponse;
    let isFallback = false;
    try {
      fresh = await refresh(env);
    } catch (e) {
      console.error("refresh failed:", e);
      fresh = { generated_at: nowIso(), currently_tending: [] };
      isFallback = true;
    }

    const ttl = isFallback ? FALLBACK_TTL_SECONDS : CACHE_TTL_SECONDS;
    const response = jsonResponse(fresh, env, ttl);
    // Cloudflare edge cache uses the response's Cache-Control header.
    ctx.waitUntil(caches.default.put(cacheKey, response.clone()));
    return response;
  },
};

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function refresh(env: Env): Promise<CurrentlyTendingResponse> {
  const consumers = await getConsumers(env);
  const perRepo = await Promise.all(
    consumers.map((c) => fetchRepoRuns(c.repo, env.GITHUB_TOKEN)),
  );
  const entries = perRepo.flat();
  // Stable secondary key so ties are deterministic across deploys.
  entries.sort((a, b) => {
    if (a.started_at !== b.started_at) {
      return a.started_at < b.started_at ? 1 : -1;
    }
    return a.repo < b.repo ? -1 : 1;
  });
  return {
    generated_at: nowIso(),
    currently_tending: entries,
  };
}

async function fetchWithTimeout(input: string, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function getConsumers(env: Env): Promise<Consumer[]> {
  // KV cache lookup is best-effort — an outage shouldn't 500 the client.
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
    // Don't cache garbage — KV would poison every viewer for an hour.
    throw new Error("consumers.json failed shape validation");
  }
  // Best-effort KV write — same reasoning as the read.
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
        isValidRepo((e as { repo: string }).repo),
    )
  );
}

async function fetchRepoRuns(
  repo: string,
  token: string,
): Promise<CurrentlyTendingEntry[]> {
  // Defense in depth — getConsumers already rejects bad shapes, but the
  // path is concatenated into a URL so a stray ".." would matter.
  if (!isValidRepo(repo)) {
    console.error(`skipping malformed repo: ${repo}`);
    return [];
  }
  const url =
    `${GITHUB_API}/repos/${repo}/actions/runs` +
    `?status=in_progress&per_page=${PER_PAGE}`;
  const resp = await fetchWithTimeout(url, {
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "User-Agent": "tend-currently-worker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!resp.ok) {
    // 401/403 means the PAT is broken (revoked, expired, wrong scope) —
    // surface so the whole response degrades to empty instead of silently
    // serving a partial result that looks like "nothing is tending."
    // 404 (repo gone) and 5xx (transient) are fine to skip.
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

function withCors(resp: Response, env: Env): Response {
  resp.headers.set("Access-Control-Allow-Origin", env.ALLOWED_ORIGIN);
  return resp;
}

function jsonResponse(
  data: unknown,
  env: Env,
  ttlSeconds: number = CACHE_TTL_SECONDS,
): Response {
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
  refresh,
  fetchRepoRuns,
  getConsumers,
  isConsumerArray,
  isValidRepo,
};
