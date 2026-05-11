// Cloudflare Worker that serves the "currently tending" data stream.
//
// Reads in-progress tend-* workflow runs across opt-in repos, caches the
// result in Workers KV for 30s, and returns CORS-enabled JSON to the tend
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

interface Repo {
  owner: string;
  repo: string;
}

interface WorkflowRun {
  name?: string;
  run_started_at: string;
  html_url: string;
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

const CACHE_KEY = "currently-tending:v1";
const REPOS_KEY = "repos:v1";
const CACHE_TTL_SECONDS = 30;
const REPOS_TTL_SECONDS = 3600;
const WORKFLOW_PREFIX = "tend-";
const PER_PAGE = 5;
const GITHUB_API = "https://api.github.com";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (request.method === "OPTIONS") {
      return corsPreflight(env);
    }
    if (request.method !== "GET") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/" && url.pathname !== "/currently-tending") {
      return new Response("Not Found", { status: 404 });
    }

    const cached = await env.CACHE.get<CurrentlyTendingResponse>(CACHE_KEY, "json");
    if (cached) {
      return jsonResponse(cached, env);
    }

    const fresh = await refresh(env);
    // Write to KV in the background so we don't delay the response.
    ctx.waitUntil(
      env.CACHE.put(CACHE_KEY, JSON.stringify(fresh), {
        expirationTtl: CACHE_TTL_SECONDS,
      }),
    );
    return jsonResponse(fresh, env);
  },
};

async function refresh(env: Env): Promise<CurrentlyTendingResponse> {
  const repos = await getRepos(env);
  const perRepo = await Promise.all(
    repos.map((r) => fetchRepoRuns(r, env.GITHUB_TOKEN)),
  );
  const entries = perRepo.flat();
  entries.sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
  return {
    generated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    currently_tending: entries,
  };
}

async function getRepos(env: Env): Promise<Repo[]> {
  const cached = await env.CACHE.get<Repo[]>(REPOS_KEY, "json");
  if (cached) return cached;

  const resp = await fetch(env.REPOS_URL, {
    cf: { cacheTtl: REPOS_TTL_SECONDS },
  });
  if (!resp.ok) {
    throw new Error(`repos.json fetch failed: ${resp.status}`);
  }
  const repos = (await resp.json()) as Repo[];
  await env.CACHE.put(REPOS_KEY, JSON.stringify(repos), {
    expirationTtl: REPOS_TTL_SECONDS,
  });
  return repos;
}

async function fetchRepoRuns(
  repo: Repo,
  token: string,
): Promise<CurrentlyTendingEntry[]> {
  const url =
    `${GITHUB_API}/repos/${repo.owner}/${repo.repo}/actions/runs` +
    `?status=in_progress&per_page=${PER_PAGE}`;
  const resp = await fetch(url, {
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "User-Agent": "tend-currently-worker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!resp.ok) {
    console.error(`runs fetch failed for ${repo.owner}/${repo.repo}: ${resp.status}`);
    return [];
  }
  const data = (await resp.json()) as RunsResponse;
  return (data.workflow_runs ?? [])
    .filter((run) => (run.name ?? "").startsWith(WORKFLOW_PREFIX))
    .map((run) => ({
      repo: `${repo.owner}/${repo.repo}`,
      workflow: run.name!,
      started_at: run.run_started_at,
      run_url: run.html_url,
    }));
}

function jsonResponse(data: unknown, env: Env): Response {
  return new Response(JSON.stringify(data), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": `public, max-age=${CACHE_TTL_SECONDS}`,
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
      Vary: "Origin",
    },
  });
}

function corsPreflight(env: Env): Response {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Max-Age": "86400",
    },
  });
}

// Exported for unit tests.
export const __test = { refresh, fetchRepoRuns, getRepos };
