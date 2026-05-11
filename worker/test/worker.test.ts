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
        "https://api.github.com/repos/o/r/actions/runs?status=in_progress&per_page=30",
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

describe("refreshCurrentlyTending", () => {
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
        "https://api.github.com/repos/max-sixty/tend/actions/runs?status=in_progress&per_page=30",
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
        "https://api.github.com/repos/PRQL/prql/actions/runs?status=in_progress&per_page=30",
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
    const out = await __test.refreshCurrentlyTending(env);
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

describe("refreshActivity", () => {
  it("merges across kinds + bots, dedupes by URL (first kind wins), caps and sorts", async () => {
    const { __test } = await import("../src/index");
    // ci-fix for bot-a returns a PR; review for bot-b returns the SAME PR
    // (bot-b commented on bot-a's CI-fix). Dedup must keep the ci-fix entry.
    const dup = {
      html_url: "https://github.com/o/r/pull/100",
      title: "fix: x",
      updated_at: "2026-05-09T12:00:00Z",
      repository_url: "https://api.github.com/repos/o/r",
    };
    const responses = new Map<string, unknown>([
      [
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
        [
          { repo: "o/r", bot_name: "bot-a" },
          { repo: "o/r", bot_name: "bot-b" },
        ],
      ],
      // bots are sorted alphabetically internally → bot-a, bot-b
      // kinds iterate in declared order: ci-fix → review → triage
      [
        "https://api.github.com/search/issues?q=author%3Abot-a+is%3Apr&per_page=10&sort=updated&order=desc",
        { items: [dup] },
      ],
      [
        "https://api.github.com/search/issues?q=author%3Abot-b+is%3Apr&per_page=10&sort=updated&order=desc",
        { items: [] },
      ],
      [
        "https://api.github.com/search/issues?q=commenter%3Abot-a+is%3Apr+-author%3Abot-a&per_page=10&sort=updated&order=desc",
        { items: [] },
      ],
      [
        "https://api.github.com/search/issues?q=commenter%3Abot-b+is%3Apr+-author%3Abot-b&per_page=10&sort=updated&order=desc",
        { items: [dup] },
      ],
      [
        "https://api.github.com/search/issues?q=commenter%3Abot-a+is%3Aissue&per_page=10&sort=updated&order=desc",
        {
          items: [
            {
              html_url: "https://github.com/o/r/issues/5",
              title: "bug report",
              updated_at: "2026-05-10T01:00:00Z",
              repository_url: "https://api.github.com/repos/o/r",
            },
          ],
        },
      ],
      [
        "https://api.github.com/search/issues?q=commenter%3Abot-b+is%3Aissue&per_page=10&sort=updated&order=desc",
        { items: [] },
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
    const out = await __test.refreshActivity(env);
    expect(out.events).toEqual([
      {
        repo: "o/r",
        kind: "triage",
        title: "bug report",
        url: "https://github.com/o/r/issues/5",
        at: "2026-05-10T01:00:00Z",
      },
      {
        repo: "o/r",
        kind: "ci-fix",
        title: "fix: x",
        url: "https://github.com/o/r/pull/100",
        at: "2026-05-09T12:00:00Z",
      },
    ]);
    expect(out.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });

  it("returns empty when no consumers", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = makeFetch(
      new Map([[
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
        [],
      ]]),
    ) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: makeFakeKv(),
      ALLOWED_ORIGIN: "*",
      REPOS_URL:
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
    };
    const out = await __test.refreshActivity(env);
    expect(out.events).toEqual([]);
  });
});

describe("refreshStats", () => {
  it("sums total_count across bots for each counter", async () => {
    const { __test } = await import("../src/index");
    // Match by URL prefix — week-windowed queries embed today's date.
    const fixedTotals: Record<string, number> = {
      "author%3Abot-a+is%3Apr": 10,
      "author%3Abot-b+is%3Apr": 4,
      "commenter%3Abot-a+is%3Apr+-author%3Abot-a": 7,
      "commenter%3Abot-b+is%3Apr+-author%3Abot-b": 3,
      "commenter%3Abot-a+is%3Aissue": 2,
      "commenter%3Abot-b+is%3Aissue": 1,
    };
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/data/consumers.json")) {
        return new Response(
          JSON.stringify([
            { repo: "o/r", bot_name: "bot-a" },
            { repo: "o/r", bot_name: "bot-b" },
          ]),
          { status: 200 },
        );
      }
      // Pull total_count by matching the substring of the query body.
      for (const [needle, total] of Object.entries(fixedTotals)) {
        if (url.includes(needle)) {
          // "this_week" queries also contain the needle but additionally
          // include `updated:>=`. Treat them as 0 to make the assertion
          // distinguishable.
          if (url.includes("updated%3A%3E%3D")) {
            return new Response(JSON.stringify({ total_count: 0 }), { status: 200 });
          }
          return new Response(JSON.stringify({ total_count: total }), { status: 200 });
        }
      }
      throw new Error(`unexpected fetch ${url}`);
    }) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: makeFakeKv(),
      ALLOWED_ORIGIN: "*",
      REPOS_URL:
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
    };
    const out = await __test.refreshStats(env);
    expect(out.ci_fixes_total).toBe(14); // 10 + 4
    expect(out.ci_fixes_this_week).toBe(0);
    expect(out.reviews_total).toBe(10); // 7 + 3
    expect(out.reviews_this_week).toBe(0);
    expect(out.triage_comments_total).toBe(3); // 2 + 1
    expect(out.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });
});

describe("isConsumerArray (shape validation)", () => {
  it("accepts valid consumers", async () => {
    const { __test } = await import("../src/index");
    expect(
      __test.isConsumerArray([{ repo: "max-sixty/tend", bot_name: "tend-agent" }]),
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

  it("rejects bot names that would break Search query syntax", async () => {
    const { __test } = await import("../src/index");
    for (const bad of ["", "bot space", "bot/slash", "bot:colon", "-leading", "."]) {
      expect(__test.isValidBotName(bad), `should reject ${bad}`).toBe(false);
    }
    for (const good of ["tend-agent", "bot_1", "PRQL-bot", "a"]) {
      expect(__test.isValidBotName(good), `should accept ${good}`).toBe(true);
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
