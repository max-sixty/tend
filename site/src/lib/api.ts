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
// null on failure) to `render`, and transition the element with id `sectionId`
// from its initial `data-state="loading"` to either `"loaded"` (render returned
// true) or `"hidden"` (false). CSS uses these states to reserve layout space
// during loading, fade content in on success, and collapse on failure — so
// the page doesn't shift when the fetch resolves.
//
// Pass `intervalMs` to keep refetching on a timer — without it the section is
// frozen as of page load, which lies after the data changes (a "live" pulse
// kept animating long after the underlying job finished, etc.). Refreshes
// re-run `render`, so the section can transition between live and idle states
// across the page's lifetime.
export async function liveData<T>(
  path: string,
  sectionId: string,
  render: (data: T | null, el: HTMLElement) => boolean | Promise<boolean>,
  intervalMs?: number,
): Promise<void> {
  const el = document.getElementById(sectionId);
  if (!el) return;
  const tick = async () => {
    const shown = await render(await fetchJson<T>(path), el);
    el.dataset.state = shown ? "loaded" : "hidden";
  };
  await tick();
  // Self-schedule so the next tick can't start before the previous one
  // resolves — otherwise a slow fetch overlapping a fast one can land
  // out of order and overwrite `dataset.state` with stale data.
  if (intervalMs && intervalMs > 0) {
    const loop = async () => {
      await tick();
      setTimeout(loop, intervalMs);
    };
    setTimeout(loop, intervalMs);
  }
}
