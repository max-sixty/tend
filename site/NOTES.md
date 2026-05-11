# Astro prototype — honest assessment

Built in one session, roughly 40 minutes wall clock. Most of that was
deciding on the editorial layout (marginalia + roman numerals + serif
italics + lowercase wordmark) and hand-shaping the SVG seedling. The
Astro mechanics — install, scaffold, layout, page, component, scoped
CSS, build — took maybe ten minutes of the total.

## What Astro gave me that Zola probably wouldn't

- **Component scoping out of the box.** The seedling SVG and its
  keyframes live in one `.astro` file. The animation CSS is scoped to
  that component; the global stylesheet stays clean. With Zola you'd
  either inline the styles in a template (worktrunk does this) or push
  them into the SCSS file and lose locality.
- **A real templating language, not just Tera.** Mapping over the five
  areas with `{areas.map(...)}` plus a typed object is dramatically
  nicer than Tera's `{% for area in areas %}` plus a TOML data file. For
  a one-page site this is a small win; for a site with many
  content-shaped lists it would matter more.
- **TypeScript by default** for any props I want to type. Probably
  overkill at this size; would matter at the live-data layer.
- **No build step to worry about for SVG.** It's just markup in an
  `.astro` file. With Zola I'd have made it a `static/seedling.svg` and
  managed CSS separately.

## Where Astro feels heavyweight

- **278 packages installed, ~40 seconds to install,** for what is
  literally a single HTML file in production. The `dist/` output is
  small and clean, but the dev toolchain is a Node project.
- **Two-step install before anything renders.** With Zola (or pure
  HTML) the friction-to-first-paint is lower.
- **Hot reload is slower than `zola serve`** in my experience (Vite is
  fast, but `zola serve` is essentially instant). On a one-pager you
  don't notice.
- **You inherit a dependency surface that has to be maintained.** This
  is the bit that will bite over time: `npm audit` will keep
  complaining, dependabot will keep filing PRs, and Node major-version
  bumps will eventually need attention. Zola is a single static binary;
  there is no audit surface.

## What the next 80% looks like

| Feature | Astro | Zola |
| --- | --- | --- |
| More content pages | File-based routing, trivial. MDX is one integration away. | Markdown in `content/`, trivial. |
| Build-time data fetch (stats, activity feed) | Top-level `await fetch(...)` in a `.astro` file. Native. | Needs a `load_data` shortcode or an external script that writes JSON. Less ergonomic. |
| Client-side polling ("currently tending") | `<script>` in component, scoped. Fine. | Same. Both end up writing vanilla JS. |
| Search | `pagefind` integration, ~10 lines. | Same `pagefind` recipe. |
| OG cards / structured data | `getImage` + a small generation step. | Tera template + a script. |
| Dark mode | Already works via `prefers-color-scheme`. Both stacks identical. | Same. |

The decisive question is **how much live data you want**. Astro's
build-time data story is genuinely nicer — you write `const stats =
await fetch(...)` at the top of your page and you're done. In Zola
you're writing a nightly Action that commits JSON, then a Tera template
that reads it, which works but feels like two systems duct-taped.

## Would I recommend Astro for tend?

For the current scope (one page, five sections, one logo animation):
**no — Zola is the better fit.** Reasons:

1. **Parity with worktrunk** is a real win, not a marketing wish.
   The two sites being siblings means CSS-fix work in one trivially
   moves to the other. Astro and Zola don't share templates.
2. **Zero runtime maintenance** matters when the bot maintaining the
   site has finite token budget. A static-binary toolchain doesn't
   generate dependency-update churn.
3. **Tend's content surface is small.** The advantages Astro gives —
   components, MDX, JSX-shaped data — are most valuable when you have
   many content shapes. tend.dev does not.

**Astro becomes the better answer if either:**

- The site grows substantially (multi-section docs, blog, MDX-heavy
  explainers), or
- The live-data layer (stats, activity feed, currently-tending
  indicator) ships and turns out to need significant client-side
  interactivity — at which point Astro's island architecture is the
  cleanest path.

If neither happens, Zola wins. If both happen, Astro wins. Right now
WEBSITE.md plans for #1 to be modest and #2 to be polled-JSON
(client-side, framework-agnostic), so Zola covers both well enough.

**Pure HTML/CSS** would be a stretch for the marginalia layout and the
five-area iteration — you'd duplicate markup. It's the right choice if
you commit to keeping the site at one page forever. I wouldn't bet
that's where tend ends up.

## One thing I'd take from this prototype regardless of stack

The **marginalia layout** — roman numeral plus area name in a left
column, body text in a wider right column — reads more like a field
guide than a marketing page. It's the single design decision I'd port
back to the Zola version. The serif italic h2 (`Issue triage`, `Work
delegation`) helps too; it pushes the page toward "notebook" without
feeling precious. Worktrunk uses a Plus Jakarta Sans h2; tend could
keep that as the body face but use Newsreader italic for the area
headings to differentiate the siblings.
