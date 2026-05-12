# tend site

The tend marketing site (<https://tend-src.com>), built with Astro and
deployed to GitHub Pages via `.github/workflows/publish-site.yaml`.

## Run locally

```sh
cd site
npm install
npm run dev
```

Then open <http://localhost:4321/>. Live-data sections (stats, activity)
fetch the Worker at `api.tend-src.com`; set `PUBLIC_WORKER_URL` in
`.env.local` to point elsewhere (see `.env.example`).

## Build

```sh
npm run build      # writes static files to site/dist/
npm run preview    # serve the built site
```

## What's here

- `src/pages/index.astro` — the single-page site (hero + 5 areas + quick start + security + footer)
- `src/components/Logo.astro` — animated SVG of the tend mark: a pen traces the outline, the colour floods in behind it, then it settles with a faint breath; pass `static` for the header lockup
- `src/layouts/Base.astro` — page shell, header, footer, font preconnect
- `src/styles/global.css` — palette, typography, marginalia grid, all layout
- `src/components/Stats.astro`, `src/components/Activity.astro` — runtime-fetch the live-data Worker; hidden when empty or the fetch fails
- `src/lib/api.ts` — Worker base URL (overridable via `PUBLIC_WORKER_URL`)
- `public/logo.png`, `public/favicon.png` — copied from `../assets/`
