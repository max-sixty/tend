import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

const originalFetch = globalThis.fetch;

interface RunsResponse {
  workflow_runs: Array<{
    name: string;
    run_started_at: string;
    html_url: string;
  }>;
}

function makeFetch(responses: Map<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (!responses.has(url)) {
      throw new Error(`unexpected fetch ${url}`);
    }
    return new Response(JSON.stringify(responses.get(url)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

beforeEach(() => {
  // each test installs its own fetch
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("fetchRepoRuns", () => {
  it("filters to tend-* workflows and shapes the entry", async () => {
    const { __test } = await import("../src/index");
    const responses = new Map<string, unknown>([
      [
        "https://api.github.com/repos/o/r/actions/runs?status=in_progress&per_page=5",
        {
          workflow_runs: [
            {
              name: "tend-review",
              run_started_at: "2026-05-10T17:00:00Z",
              html_url: "https://github.com/o/r/actions/runs/1",
            },
            {
              name: "ci",
              run_started_at: "2026-05-10T17:01:00Z",
              html_url: "https://github.com/o/r/actions/runs/2",
            },
            {
              name: "tend-triage",
              run_started_at: "2026-05-10T17:02:00Z",
              html_url: "https://github.com/o/r/actions/runs/3",
            },
          ],
        } satisfies RunsResponse,
      ],
    ]);
    globalThis.fetch = makeFetch(responses) as unknown as typeof fetch;

    const result = await __test.fetchRepoRuns({ owner: "o", repo: "r" }, "token");
    expect(result).toEqual([
      {
        repo: "o/r",
        workflow: "tend-review",
        started_at: "2026-05-10T17:00:00Z",
        run_url: "https://github.com/o/r/actions/runs/1",
      },
      {
        repo: "o/r",
        workflow: "tend-triage",
        started_at: "2026-05-10T17:02:00Z",
        run_url: "https://github.com/o/r/actions/runs/3",
      },
    ]);
  });

  it("returns empty list on API failure (does not throw)", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = vi.fn(
      async () => new Response("rate limited", { status: 403 }),
    ) as unknown as typeof fetch;

    const result = await __test.fetchRepoRuns({ owner: "o", repo: "r" }, "token");
    expect(result).toEqual([]);
  });
});

describe("refresh", () => {
  it("fans out across repos and sorts newest first", async () => {
    const { __test } = await import("../src/index");
    const responses = new Map<string, unknown>([
      [
        "https://raw.githubusercontent.com/max-sixty/tend/website/static/data/repos.json",
        [
          { owner: "max-sixty", repo: "tend" },
          { owner: "max-sixty", repo: "other" },
        ],
      ],
      [
        "https://api.github.com/repos/max-sixty/tend/actions/runs?status=in_progress&per_page=5",
        {
          workflow_runs: [
            {
              name: "tend-review",
              run_started_at: "2026-05-10T10:00:00Z",
              html_url: "u1",
            },
          ],
        },
      ],
      [
        "https://api.github.com/repos/max-sixty/other/actions/runs?status=in_progress&per_page=5",
        {
          workflow_runs: [
            {
              name: "tend-triage",
              run_started_at: "2026-05-10T12:00:00Z",
              html_url: "u2",
            },
          ],
        },
      ],
    ]);
    globalThis.fetch = makeFetch(responses) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: makeFakeKv(),
      ALLOWED_ORIGIN: "https://example.test",
      REPOS_URL:
        "https://raw.githubusercontent.com/max-sixty/tend/website/static/data/repos.json",
    };
    const out = await __test.refresh(env);
    expect(out.currently_tending.map((e) => e.workflow)).toEqual([
      "tend-triage",
      "tend-review",
    ]);
    expect(out.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });
});

function makeFakeKv(): KVNamespace {
  const store = new Map<string, string>();
  return {
    async get(key: string, type?: string) {
      const v = store.get(key);
      if (v === undefined) return null;
      return type === "json" ? JSON.parse(v) : v;
    },
    async put(key: string, value: string) {
      store.set(key, value);
    },
    async delete(key: string) {
      store.delete(key);
    },
    async list() {
      return { keys: [], list_complete: true as const, cacheStatus: null };
    },
    async getWithMetadata() {
      return { value: null, metadata: null, cacheStatus: null };
    },
  } as unknown as KVNamespace;
}
