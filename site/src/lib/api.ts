// Base URL of the live-data Worker. Override via PUBLIC_WORKER_URL in
// .env.local (e.g. http://localhost:8787 against a local `wrangler dev`,
// or https://currently.tend-src.com during the tend-currently → tend-
// website migration).
export const WORKER_URL =
  (import.meta.env.PUBLIC_WORKER_URL as string | undefined) ??
  "https://api.tend-src.com";
