// Talks to the live-data Worker (the tend-website Cloudflare Worker, served at
// api.tend-src.com). Override the base URL via PUBLIC_WORKER_URL in .env.local
// — e.g. http://localhost:8787 against a local `wrangler dev`.
const WORKER_URL =
  (import.meta.env.PUBLIC_WORKER_URL as string | undefined) ??
  "https://api.tend-src.com";

// Fetch JSON from a Worker route. Returns null on any non-OK response or
// network error so callers can degrade gracefully rather than throwing.
export async function fetchJson<T>(path: string): Promise<T | null> {
  try {
    const resp = await fetch(`${WORKER_URL}${path}`, {
      headers: { Accept: "application/json" },
    });
    return resp.ok ? ((await resp.json()) as T) : null;
  } catch {
    return null;
  }
}

// Drives a runtime-fetched section: fetch `path`, hand the parsed body (or
// null on failure) to `render`, and reveal the element with id `sectionId`
// only if `render` returns true. `render` owns the DOM and the "is this worth
// showing" decision, and may issue further fetches (e.g. a fallback endpoint).
export async function liveData<T>(
  path: string,
  sectionId: string,
  render: (data: T | null, el: HTMLElement) => boolean | Promise<boolean>,
): Promise<void> {
  const el = document.getElementById(sectionId);
  if (!el) return;
  const shown = await render(await fetchJson<T>(path), el);
  if (shown) el.hidden = false;
}
