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
  // Build a Search URL the way searchRaw does: q + per_page, then sort/order
  // appended (perPage > 1).
  const searchUrl = (q: string) =>
    `https://api.github.com/search/issues?${new URLSearchParams({
      q,
      per_page: "100",
    })}&sort=updated&order=desc`;

  it("fans out one Search query per bucket per bot — sums counts, merges + sorts recent, counts this week", async () => {
    const { __test } = await import("../src/index");
    const nowMs = Date.now();
    const daysAgo = (n: number) => new Date(nowMs - n * 86_400_000).toISOString();
    const recentA = daysAgo(1); // within the last 7 days
    const recentB = daysAgo(2);
    const oldA = daysAgo(30); // not
    const oldB = daysAgo(60);

    const item = (
      repo: string,
      n: number,
      kind: "pull" | "issues",
      at: string,
    ) => ({
      html_url: `https://github.com/${repo}/${kind}/${n}`,
      title: `${repo}#${n}`,
      updated_at: at,
      repository_url: `https://api.github.com/repos/${repo}`,
    });

    const responses = new Map<string, unknown>([
      [
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
        [
          { repo: "o/a", bot_name: "bot-a" },
          { repo: "o/b", bot_name: "bot-b" },
        ],
      ],
      // bots sorted → bot-a, bot-b. Buckets in declared order: prs, issues, comments.
      [searchUrl("author:bot-a is:pr"), { total_count: 7, items: [item("o/a", 1, "pull", recentA), item("o/a", 2, "pull", oldA)] }],
      [searchUrl("author:bot-b is:pr"), { total_count: 3, items: [item("o/b", 9, "pull", oldB)] }],
      [searchUrl("author:bot-a is:issue"), { total_count: 2, items: [item("o/a", 5, "issues", recentB)] }],
      [searchUrl("author:bot-b is:issue"), { total_count: 0, items: [] }],
      [searchUrl("commenter:bot-a -author:bot-a"), { total_count: 12, items: [item("o/a", 3, "pull", recentA)] }],
      [searchUrl("commenter:bot-b -author:bot-b"), { total_count: 4, items: [item("o/b", 8, "issues", recentB)] }],
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

    expect(out.prs).toEqual({
      count: 10, // 7 + 3
      count_this_week: 1, // o/a#1 recent; o/a#2 and o/b#9 old
      recent: [
        { repo: "o/a", title: "o/a#1", url: "https://github.com/o/a/pull/1", at: recentA },
        { repo: "o/a", title: "o/a#2", url: "https://github.com/o/a/pull/2", at: oldA },
        { repo: "o/b", title: "o/b#9", url: "https://github.com/o/b/pull/9", at: oldB },
      ],
    });
    expect(out.issues).toEqual({
      count: 2,
      count_this_week: 1,
      recent: [
        { repo: "o/a", title: "o/a#5", url: "https://github.com/o/a/issues/5", at: recentB },
      ],
    });
    expect(out.comments).toEqual({
      count: 16, // 12 + 4
      count_this_week: 2,
      recent: [
        { repo: "o/a", title: "o/a#3", url: "https://github.com/o/a/pull/3", at: recentA },
        { repo: "o/b", title: "o/b#8", url: "https://github.com/o/b/issues/8", at: recentB },
      ],
    });
    expect(out.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });

  it("degrades a failed bucket query to an empty bucket without sinking the refresh", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/data/consumers.json")) {
        return new Response(
          JSON.stringify([{ repo: "o/r", bot_name: "bot-a" }]),
          { status: 200 },
        );
      }
      if (url.includes("commenter")) {
        return new Response("Validation Failed", { status: 422 });
      }
      return new Response(JSON.stringify({ total_count: 1, items: [] }), {
        status: 200,
      });
    }) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: makeFakeKv(),
      ALLOWED_ORIGIN: "*",
      REPOS_URL:
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
    };
    const out = await __test.refreshActivity(env);
    expect(out.comments).toEqual({ count: 0, count_this_week: 0, recent: [] });
    expect(out.prs.count).toBe(1);
    expect(out.issues.count).toBe(1);
  });

  it("returns empty buckets when there are no consumers", async () => {
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
    expect(out.prs).toEqual({ count: 0, count_this_week: 0, recent: [] });
    expect(out.issues).toEqual({ count: 0, count_this_week: 0, recent: [] });
    expect(out.comments).toEqual({ count: 0, count_this_week: 0, recent: [] });
  });

  it("throws on a 401 from Search — surfaces so the refresh falls back", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/data/consumers.json")) {
        return new Response(
          JSON.stringify([{ repo: "o/r", bot_name: "bot-a" }]),
          { status: 200 },
        );
      }
      return new Response("bad credentials", { status: 401 });
    }) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: makeFakeKv(),
      ALLOWED_ORIGIN: "*",
      REPOS_URL:
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
    };
    await expect(__test.refreshActivity(env)).rejects.toThrow(/auth failure/);
  });

  it("keeps only the newest RECENT_PER_BUCKET items per bucket", async () => {
    const { __test } = await import("../src/index");
    const items = Array.from({ length: 15 }, (_, i) => ({
      html_url: `https://github.com/o/r/pull/${i}`,
      title: `#${i}`,
      // i=0 is newest (largest timestamp), i=14 is oldest
      updated_at: new Date(2026, 0, 1, 0, 15 - i).toISOString(),
      repository_url: "https://api.github.com/repos/o/r",
    }));
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/data/consumers.json")) {
        return new Response(
          JSON.stringify([{ repo: "o/r", bot_name: "bot-a" }]),
          { status: 200 },
        );
      }
      const body = url.includes("is%3Apr") ? { total_count: 15, items } : { total_count: 0, items: [] };
      return new Response(JSON.stringify(body), { status: 200 });
    }) as unknown as typeof fetch;

    const env = {
      GITHUB_TOKEN: "tok",
      CACHE: makeFakeKv(),
      ALLOWED_ORIGIN: "*",
      REPOS_URL:
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
    };
    const out = await __test.refreshActivity(env);
    expect(out.prs.count).toBe(15);
    expect(out.prs.recent).toHaveLength(10);
    expect(out.prs.recent.map((r) => r.title)).toEqual(
      ["#0", "#1", "#2", "#3", "#4", "#5", "#6", "#7", "#8", "#9"], // newest first
    );
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
