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

function byAtDesc(a: { at: string }, b: { at: string }) {
  return a.at < b.at ? 1 : a.at > b.at ? -1 : 0;
}

describe("eventsToActivity", () => {
  it("maps each event type, filters foreign repos, collapses comments and pushes", async () => {
    const { __test } = await import("../src/index");
    const repos = new Set(["o/r"]);
    const ev = <P>(type: string, created_at: string, payload: P) => ({
      type,
      created_at,
      repo: { name: "o/r" },
      payload,
    });
    const out = __test
      .eventsToActivity(
        [
          // foreign repo — dropped entirely
          {
            type: "PullRequestEvent",
            created_at: "2026-05-10T00:00:00Z",
            repo: { name: "someone/else" },
            payload: {
              action: "opened",
              pull_request: { html_url: "https://github.com/someone/else/pull/1", title: "nope" },
            },
          },
          ev("PullRequestEvent", "2026-05-10T09:00:00Z", {
            action: "opened",
            pull_request: {
              html_url: "https://github.com/o/r/pull/10",
              title: "fix: flaky test",
              head: { ref: "fix/ci-12345" },
            },
          }),
          ev("PullRequestReviewEvent", "2026-05-10T08:00:00Z", {
            action: "created",
            review: { state: "approved" },
            pull_request: { html_url: "https://github.com/o/r/pull/11", title: "feat: x" },
          }),
          // dependabot PR review → dep-approved, not pr-reviewed
          ev("PullRequestReviewEvent", "2026-05-10T07:30:00Z", {
            action: "created",
            review: { state: "approved" },
            pull_request: {
              html_url: "https://github.com/o/r/pull/12",
              title: "Bump serde 1.0.1 to 1.0.2",
              user: { login: "dependabot[bot]" },
            },
          }),
          // a "commented" review + an inline comment + a conversation comment, all on #11 → collapse to count 3
          ev("PullRequestReviewEvent", "2026-05-10T08:05:00Z", {
            action: "created",
            review: { state: "commented" },
            pull_request: { html_url: "https://github.com/o/r/pull/11", title: "feat: x" },
          }),
          ev("PullRequestReviewCommentEvent", "2026-05-10T08:10:00Z", {
            action: "created",
            pull_request: { html_url: "https://github.com/o/r/pull/11", title: "feat: x" },
          }),
          ev("IssueCommentEvent", "2026-05-10T08:20:00Z", {
            action: "created",
            issue: {
              html_url: "https://github.com/o/r/pull/11",
              title: "feat: x",
              pull_request: { url: "..." },
            },
          }),
          // triage comment on a real issue
          ev("IssueCommentEvent", "2026-05-10T06:00:00Z", {
            action: "created",
            issue: { html_url: "https://github.com/o/r/issues/20", title: "bug report" },
          }),
          ev("IssuesEvent", "2026-05-10T05:00:00Z", {
            action: "closed",
            issue: { html_url: "https://github.com/o/r/issues/21", title: "resolved bug" },
          }),
          // two pushes to the same PR branch → collapse, count 1+2=3, newest at/url wins
          ev("PushEvent", "2026-05-10T04:00:00Z", {
            ref: "refs/heads/fix/ci-12345",
            head: "aaa",
            size: 1,
            commits: [{ sha: "aaa", message: "first\n\nbody" }],
          }),
          ev("PushEvent", "2026-05-10T04:30:00Z", {
            ref: "refs/heads/fix/ci-12345",
            head: "bbb",
            size: 2,
            commits: [{ sha: "ccc", message: "mid" }, { sha: "bbb", message: "second commit" }],
          }),
          // push to the default branch → ignored
          ev("PushEvent", "2026-05-10T03:00:00Z", {
            ref: "refs/heads/main",
            head: "ddd",
            size: 1,
            commits: [{ sha: "ddd", message: "direct to main" }],
          }),
        ],
        repos,
      )
      .sort(byAtDesc);

    expect(out).toEqual([
      {
        repo: "o/r",
        kind: "pr-opened",
        title: "fix: flaky test",
        url: "https://github.com/o/r/pull/10",
        at: "2026-05-10T09:00:00Z",
        detail: { category: "ci-fix" },
      },
      {
        repo: "o/r",
        kind: "pr-commented",
        title: "feat: x",
        url: "https://github.com/o/r/pull/11",
        at: "2026-05-10T08:20:00Z",
        detail: { count: 3 },
      },
      {
        repo: "o/r",
        kind: "pr-reviewed",
        title: "feat: x",
        url: "https://github.com/o/r/pull/11",
        at: "2026-05-10T08:00:00Z",
        detail: { verdict: "approved" },
      },
      {
        repo: "o/r",
        kind: "dep-approved",
        title: "Bump serde 1.0.1 to 1.0.2",
        url: "https://github.com/o/r/pull/12",
        at: "2026-05-10T07:30:00Z",
      },
      {
        repo: "o/r",
        kind: "issue-commented",
        title: "bug report",
        url: "https://github.com/o/r/issues/20",
        at: "2026-05-10T06:00:00Z",
        detail: { count: 1 },
      },
      {
        repo: "o/r",
        kind: "issue-closed",
        title: "resolved bug",
        url: "https://github.com/o/r/issues/21",
        at: "2026-05-10T05:00:00Z",
      },
      {
        repo: "o/r",
        kind: "pr-commits",
        title: "second commit",
        url: "https://github.com/o/r/commit/bbb",
        at: "2026-05-10T04:30:00Z",
        detail: { count: 3 },
      },
    ]);
  });
});

describe("prCategory", () => {
  it("infers PR category from the head-branch name", async () => {
    const { __test } = await import("../src/index");
    expect(__test.prCategory("fix/ci-99887")).toBe("ci-fix");
    expect(__test.prCategory("tend/update-workflows")).toBe("workflow");
    expect(__test.prCategory("tend/msrv-bump")).toBe("maintenance");
    expect(__test.prCategory("dependabot/cargo/serde-1.2")).toBe("other");
    expect(__test.prCategory(undefined)).toBe("other");
  });
});

describe("fetchBotEvents", () => {
  it("returns the event array on success", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = makeFetch(
      new Map<string, unknown>([
        [
          "https://api.github.com/users/bot-a/events/public?per_page=100",
          [{ type: "PushEvent", created_at: "2026-05-10T00:00:00Z" }],
        ],
      ]),
    ) as unknown as typeof fetch;
    expect(await __test.fetchBotEvents("bot-a", "tok")).toEqual([
      { type: "PushEvent", created_at: "2026-05-10T00:00:00Z" },
    ]);
  });

  it("returns empty on 404; throws on 401/403", async () => {
    const { __test } = await import("../src/index");
    globalThis.fetch = vi.fn(
      async () => new Response("not found", { status: 404 }),
    ) as unknown as typeof fetch;
    expect(await __test.fetchBotEvents("bot-a", "tok")).toEqual([]);

    globalThis.fetch = vi.fn(
      async () => new Response("bad credentials", { status: 401 }),
    ) as unknown as typeof fetch;
    await expect(__test.fetchBotEvents("bot-a", "tok")).rejects.toThrow(
      /auth failure/,
    );
  });
});

describe("refreshActivity", () => {
  it("fans out events + merged-PR search per bot, sorts newest first, keeps a PR that both opened and merged", async () => {
    const { __test } = await import("../src/index");
    const responses = new Map<string, unknown>([
      [
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
        [{ repo: "o/r", bot_name: "bot-a" }],
      ],
      [
        "https://api.github.com/users/bot-a/events/public?per_page=100",
        [
          {
            type: "PullRequestEvent",
            created_at: "2026-05-09T10:00:00Z",
            repo: { name: "o/r" },
            payload: {
              action: "opened",
              pull_request: {
                html_url: "https://github.com/o/r/pull/30",
                title: "fix: thing",
                head: { ref: "fix/ci-7" },
              },
            },
          },
          {
            type: "IssuesEvent",
            created_at: "2026-05-11T10:00:00Z",
            repo: { name: "o/r" },
            payload: {
              action: "closed",
              issue: { html_url: "https://github.com/o/r/issues/31", title: "stale issue" },
            },
          },
        ],
      ],
      [
        "https://api.github.com/search/issues?q=author%3Abot-a+is%3Apr+is%3Amerged&per_page=10&sort=updated&order=desc",
        {
          items: [
            {
              html_url: "https://github.com/o/r/pull/30",
              title: "fix: thing",
              updated_at: "2026-05-10T12:00:00Z",
              repository_url: "https://api.github.com/repos/o/r",
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
    const out = await __test.refreshActivity(env);
    expect(out.events).toEqual([
      {
        repo: "o/r",
        kind: "issue-closed",
        title: "stale issue",
        url: "https://github.com/o/r/issues/31",
        at: "2026-05-11T10:00:00Z",
      },
      {
        repo: "o/r",
        kind: "pr-merged",
        title: "fix: thing",
        url: "https://github.com/o/r/pull/30",
        at: "2026-05-10T12:00:00Z",
      },
      {
        repo: "o/r",
        kind: "pr-opened",
        title: "fix: thing",
        url: "https://github.com/o/r/pull/30",
        at: "2026-05-09T10:00:00Z",
        detail: { category: "ci-fix" },
      },
    ]);
    expect(out.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });

  it("merges two bots' streams, dedups same-kind/url rows (newest wins), drops foreign repos", async () => {
    const { __test } = await import("../src/index");
    const responses = new Map<string, unknown>([
      [
        "https://raw.githubusercontent.com/max-sixty/tend/main/data/consumers.json",
        [
          { repo: "o/r", bot_name: "bot-a" },
          { repo: "o/r", bot_name: "bot-b" },
        ],
      ],
      [
        "https://api.github.com/users/bot-a/events/public?per_page=100",
        [
          {
            type: "PullRequestReviewEvent",
            created_at: "2026-05-10T01:00:00Z",
            repo: { name: "o/r" },
            payload: {
              action: "created",
              review: { state: "approved" },
              pull_request: { html_url: "https://github.com/o/r/pull/5", title: "feat: shared" },
            },
          },
          {
            type: "IssuesEvent",
            created_at: "2026-05-10T03:00:00Z",
            repo: { name: "o/r" },
            payload: {
              action: "closed",
              issue: { html_url: "https://github.com/o/r/issues/9", title: "old bug" },
            },
          },
          // foreign repo — dropped
          {
            type: "IssuesEvent",
            created_at: "2026-05-10T05:00:00Z",
            repo: { name: "o/other" },
            payload: {
              action: "closed",
              issue: { html_url: "https://github.com/o/other/issues/1", title: "nope" },
            },
          },
        ],
      ],
      [
        "https://api.github.com/users/bot-b/events/public?per_page=100",
        [
          // same PR as bot-a's review, newer timestamp → dedup keeps this one
          {
            type: "PullRequestReviewEvent",
            created_at: "2026-05-10T02:00:00Z",
            repo: { name: "o/r" },
            payload: {
              action: "created",
              review: { state: "approved" },
              pull_request: { html_url: "https://github.com/o/r/pull/5", title: "feat: shared" },
            },
          },
          {
            type: "PullRequestEvent",
            created_at: "2026-05-10T00:00:00Z",
            repo: { name: "o/r" },
            payload: {
              action: "opened",
              pull_request: {
                html_url: "https://github.com/o/r/pull/6",
                title: "chore: cache audit",
                head: { ref: "tend/cache-audit" },
              },
            },
          },
        ],
      ],
      [
        "https://api.github.com/search/issues?q=author%3Abot-a+is%3Apr+is%3Amerged&per_page=10&sort=updated&order=desc",
        { items: [] },
      ],
      [
        "https://api.github.com/search/issues?q=author%3Abot-b+is%3Apr+is%3Amerged&per_page=10&sort=updated&order=desc",
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
        kind: "issue-closed",
        title: "old bug",
        url: "https://github.com/o/r/issues/9",
        at: "2026-05-10T03:00:00Z",
      },
      {
        repo: "o/r",
        kind: "pr-reviewed",
        title: "feat: shared",
        url: "https://github.com/o/r/pull/5",
        at: "2026-05-10T02:00:00Z",
        detail: { verdict: "approved" },
      },
      {
        repo: "o/r",
        kind: "pr-opened",
        title: "chore: cache audit",
        url: "https://github.com/o/r/pull/6",
        at: "2026-05-10T00:00:00Z",
        detail: { category: "maintenance" },
      },
    ]);
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
