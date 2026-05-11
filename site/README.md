# tend site — Astro prototype

Stage-1 prototype of the tend website, built with Astro.

## Run locally

```sh
cd site
npm install
npm run dev
```

Then open <http://localhost:4321/>.

## Build

```sh
npm run build      # writes static files to site/dist/
npm run preview    # serve the built site
```

## What's here

- `src/pages/index.astro` — the single-page site (hero + 5 areas + quick start + security + footer)
- `src/components/Logo.astro` — animated SVG of the tend mark (grows from the ground, then breathes); pass `static` for the header lockup
- `src/layouts/Base.astro` — page shell, header, footer, font preconnect
- `src/styles/global.css` — palette, typography, marginalia grid, all layout
- `public/logo.png`, `public/favicon.png` — copied from `../assets/`

See `NOTES.md` for an honest assessment of Astro vs. Zola / pure HTML at this scale.
