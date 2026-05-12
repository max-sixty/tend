// Base URL of the live-data Worker (the tend-website Cloudflare Worker).
// Override via PUBLIC_WORKER_URL in .env.local — e.g. http://localhost:8787
// against a local `wrangler dev`.
export const WORKER_URL =
  (import.meta.env.PUBLIC_WORKER_URL as string | undefined) ??
  "https://api.tend-src.com";
