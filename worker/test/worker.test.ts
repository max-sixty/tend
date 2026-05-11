import { describe, expect, it, vi, afterEach } from "vitest";

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

    const result = await __test.fetchRepoRuns("o/r", "token");
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

  it("returns empty on 404 (repo gone) — does not throw", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = vi.fn(
      async () => new Response("not found", { status: 404 }),
    ) as unknown as typeof fetch;

    const result = await __test.fetchRepoRuns("o/r", "token");
    expect(result).toEqual([]);
  });

  it("throws on 401/403 (auth problem) — surfaces to caller", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = vi.fn(
      async () => new Response("bad credentials", { status: 401 }),
    ) as unknown as typeof fetch;

    await expect(__test.fetchRepoRuns("o/r", "token")).rejects.toThrow(
      /auth failure/,
    );
  });
});

describe("refresh", () => {
  it("fans out across consumers and sorts newest first", async () => {
    const { __test } = await import("../src/index");
    const responses = new Map<string, unknown>([
      [
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
        [
          { repo: "max-sixty/tend", bot_name: "tend-agent" },
          { repo: "PRQL/prql", bot_name: "prql-bot" },
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
        "https://api.github.com/repos/PRQL/prql/actions/runs?status=in_progress&per_page=5",
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
      ALLOWED_ORIGIN: "*",
      REPOS_URL:
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
    };
    const out = await __test.refresh(env);
    expect(out.currently_tending).toEqual([
      {
        repo: "PRQL/prql",
        workflow: "tend-triage",
        started_at: "2026-05-10T12:00:00Z",
        run_url: "u2",
      },
      {
        repo: "max-sixty/tend",
        workflow: "tend-review",
        started_at: "2026-05-10T10:00:00Z",
        run_url: "u1",
      },
    ]);
    expect(out.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });
});

describe("isConsumerArray (shape validation)", () => {
  it("accepts valid consumers", async () => {
    const { __test } = await import("../src/index");
    expect(
      __test.isConsumerArray([
        { repo: "max-sixty/tend", bot_name: "tend-agent" },
      ]),
    ).toBe(true);
  });

  it("rejects path-traversal repo values", async () => {
    const { __test } = await import("../src/index");
    for (const bad of [
      "../etc/passwd",
      "../foo/bar",
      "foo/..",
      "../..",
      "./b",
      "a/..b",
      "a/b..",
      ".hidden/repo",
      "-leading/repo",
      "a/-leading",
      "a/b/c",
      "no-slash",
    ]) {
      expect(
        __test.isConsumerArray([{ repo: bad, bot_name: "x" }]),
        `should reject ${bad}`,
      ).toBe(false);
    }
  });

  it("isValidRepo accepts realistic GitHub repos", async () => {
    const { __test } = await import("../src/index");
    for (const good of [
      "max-sixty/tend",
      "PRQL/prql",
      "max-sixty/cargo-affected",
      "numbagg/numbagg",
      "a/b",
      "org_with_underscore/repo.with.dots",
    ]) {
      expect(__test.isValidRepo(good), `should accept ${good}`).toBe(true);
    }
  });

  it("rejects non-arrays and malformed entries", async () => {
    const { __test } = await import("../src/index");
    expect(__test.isConsumerArray({ repo: "o/r" })).toBe(false);
    expect(__test.isConsumerArray([{ not_repo: "x" }])).toBe(false);
    expect(__test.isConsumerArray([null])).toBe(false);
    expect(__test.isConsumerArray(null)).toBe(false);
  });
});

describe("getConsumers", () => {
  it("rejects malformed JSON shape (does not poison KV)", async () => {
    const { __test } = await import("../src/index");
    const kv = makeFakeKv();
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([{ not_repo: "x" }]), { status: 200 }),
    ) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: kv,
      ALLOWED_ORIGIN: "*",
      REPOS_URL: "https://example.test/consumers.json",
    };
    await expect(__test.getConsumers(env)).rejects.toThrow(/shape validation/);
    // KV must NOT have been written.
    expect(await kv.get("repos:v1")).toBeNull();
  });

  it("rejects non-array body", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ repo: "o/r" }), { status: 200 }),
    ) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: makeFakeKv(),
      ALLOWED_ORIGIN: "*",
      REPOS_URL: "https://example.test/consumers.json",
    };
    await expect(__test.getConsumers(env)).rejects.toThrow();
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
