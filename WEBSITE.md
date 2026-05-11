# Tend website — plan

Planning doc for building `tend`'s site. Each work-item under "Segmentation" is
intended to be self-contained briefing for a separate Claude session running in
parallel.

## Vision

Sibling to [worktrunk.dev](https://worktrunk.dev) but pulled toward
**garden / herbarium / tended bed** rather than worktrunk's "sunlit workbench."
The tend logo is already a seedling-as-lowercase-`t` in pale oak (`#b5a48a`);
the site extends that metaphor.

Anti-slop principles (these are the ones marketing sites usually fail):

- **One canonical accent**, not a palette of gradients.
- **One animation**, not three.
- **Substance early** — show what tend actually does, with real artifacts, on
  the first scroll. No "build the future of X" tagline.
- **No generic feature grid** of stock-icon cards.
- **No particle backgrounds, glassmorphism, parallax, typewriter taglines, or
  animated gradient blobs.**
- Restraint over polish; sparse over dense.

### Palette

- **Paper**: warm off-white, slightly cooler than worktrunk's `#faf9f7`.
- **Moss** (primary accent): `~#3f6b46`. Used for links, headings on hover,
  the seedling leaves in the animation.
- **Oak / sand** (brand mark): `#b5a48a`, matches the logo.
- **Loam** (heading underlines, dividers): `~#4a3a2a`.
- Dark mode: warm dark `~#1c1b1a` base, brighter moss `~#7fb389` for links.

### Typography

Default to worktrunk's stack (Inter / Plus Jakarta Sans / JetBrains Mono) so the
two sites read as siblings. Optional: try a humanist serif (Newsreader / Source
Serif) for headings to push tend toward "field notes" — decide during the theme
session.

## Stack

- **Zola**, hosted on **GitHub Pages** from the `website` branch.
- Reasons: parity with worktrunk (theme code transfers), no runtime required,
  cheap to host, build-time data fetching is straightforward via a nightly
  GitHub Action.
- Alternative considered: Astro. Rejected for MVP — adds moving parts without
  payoff until the live-data layer ships.

## Information architecture

The README's 8-workflow table is too implementation-level for the site
(triggers, workflow names). Speak at the level of **areas tend tends**:

1. **Hero** — logo, one-line tagline, install command, GitHub stars. Seedling
   unfurl animation runs once on load.
2. **What tend tends** — five areas, each a short section with heading and
   2–3 sentences:
   - **PR reviews** — reads PRs, traces error paths, comments on correctness
     and duplication, pushes mechanical fixes to bot-authored PRs.
   - **Issue triage** — classifies new issues, checks for duplicates,
     reproduces bugs, attempts conservative fixes.
   - **Work delegation** — responds to `@bot` mentions in PR and issue threads;
     the human stays in the loop, tend does the grunt work.
   - **Nightly & weekly maintenance** — resolves conflicts on open PRs,
     surveys files for stale docs and small bugs, closes resolved issues,
     reviews dependency PRs.
   - **Docs & code quality audits** — reviews recent commits, reviews recent
     CI runs for behavioral problems, proposes skill and config improvements.
3. **See it work** — 2–3 real screenshots of tend output: a review comment,
   a CI fix PR, a triage response. Vertical sequence with captions, not a
   carousel.
4. **Quick start** — the three commands, exactly as in README.
5. **Security model** — one paragraph + link to `docs/security-model.md`.
   Every adopter asks this question.
6. **Footer** — GitHub, PyPI, share links, license.

## Animation

**One** animation, on the logo only.

- **Seedling unfurl**: the `t` draws itself in. Stem rises from the bottom
  curve, the two crossbar leaves unfurl outward, the bud opens at the top.
  ~1.2s, single play on page load (with `prefers-reduced-motion` opt-out).
- Implementation: SVG path with `stroke-dashoffset` for the line work + CSS
  keyframes for the leaf scale/rotate. No JS library.
- Secondary touch (free with the theme): hand-drawn ink-stroke underlines
  on section headings on scroll-in. Already established in worktrunk's CSS.

## Live data

Built. See [`docs/website-data.md`](docs/website-data.md) for full schemas
and dataflow. Summary:

1. **Stat strip** — counts aggregated across all tend bots in
   `data/consumers.json`. Daily GitHub Action writes `data/stats.json`.
   Falls back to hidden if values are zero.
2. **Recent activity feed** — top 10 recent issues/PRs touched by any tend
   bot, grouped by kind (ci-fix / review / triage). Daily Action writes
   `data/activity.json`.
3. **Currently tending** indicator — Cloudflare Worker at
   `https://tend-currently.maxsixty.workers.dev` fans out to in-progress
   `actions/runs` per consumer repo, 30 s KV cache. Graceful fallback to
   the latest event in `activity.json` when nothing is running.

## Segmentation — parallel work

Each numbered item is a discrete session. Items in the same phase have no
dependencies on each other and can run concurrently.

### Phase 1 — scaffold (must land before phase 2 begins)

1. **Zola scaffold.** Copy structure from `../worktrunk/docs` (or clone fresh
   from a Zola starter and lift just the bits we want). Strip worktrunk-
   specific content from `content/` and template logic that doesn't apply.
   Wire up: `config.toml` (title=Tend, base_url=TBD), GitHub Actions workflow
   that builds on push to `website` and deploys to GitHub Pages, local
   `zola serve` dev workflow documented in a `README.md` for the docs/site
   folder. **Do not** carry over worktrunk's amber palette or hero — leave a
   plain placeholder for the theme/hero sessions to fill in. Result: a clean
   site that builds, deploys, and shows a single "Tend" h1.

### Phase 2 — parallel (after #1 lands)

2. **Theme.** Owns `templates/_variables.html` (CSS custom properties) and
   `sass/custom.scss`. Rebrand to the moss / oak / loam palette above. Light
   and dark modes both. Do **not** touch content files or templates beyond
   `_variables.html`. Try a serif-headings option in a branch and decide
   alongside the user.

3. **Hero + seedling animation.** Owns the hero block in `templates/index.html`
   and a new `static/seedling.svg`. Build the SVG (the `t` decomposed into
   stem path, two crossbar leaves, bud) and the unfurl keyframes. Include
   `prefers-reduced-motion` opt-out. Hard-code tagline copy as a placeholder;
   final copy lands in #4.

4. **Content: "What tend tends" section.** Authors `content/_index.md` (or
   equivalent) with the five areas above. Voice: concrete, restrained,
   first-person plural avoided. Pull raw material from the README workflow
   descriptions but rewrite to the area-level abstraction. Each area gets a
   heading + 2–3 sentences. Small leaf-glyph or no icon at all — pick during
   the session.

5. **Content: Quick start + Security.** Authors the install commands section
   (lifted from README §"Quick start") and a one-paragraph security summary
   that links out to `docs/security-model.md`. Keep prose tight; don't
   restate the README.

6. **"See it work" placeholder.** Adds a section with three captioned image
   slots and writes the captions. Images themselves can be sourced later;
   for now use grey placeholders (1200×800) so layout is locked.
   Capture target: a tend-agent review comment, a tend-opened CI-fix PR, a
   triage response. Prefer screenshots from tend's own repo to avoid asking
   third-party repo owners for permission.

7. **Meta + favicons + social.** Wire up favicons from `assets/logo-32.png`,
   `logo-64.png`, `apple-touch-icon` from `logo-256.png`. OG card + Twitter
   card (1200×630 with logo + tagline). `application/ld+json`
   `SoftwareApplication` schema mirrored from worktrunk's. Robots, sitemap.

### Phase 3 — live data (can start once #1 lands; renders depend on #2)

8. **GitHub data fetcher.** ✅ Built. `scripts/fetch_website_data.py` is
   invoked by the tend bot each night via `running-tend`'s skill; the bot
   commits `data/activity.json` and `data/stats.json` to `main` if they
   changed. Iterates each entry in `data/consumers.json` for per-bot
   Search queries; aggregates results.

9. **Stat strip rendering.** Reads `data/stats.json` at build time (Astro)
   or via fetch (Zola). Renders a strip of 4–5 stats below the hero. Hidden
   entirely if values are zero. Visual: small numerals, thin labels, no
   card chrome.

10. **Activity feed rendering.** Reads `data/activity.json`, renders up to
    10 compact rows (repo · title · timestamp). Each row links to the
    source. No avatars, no thumbnails.

### Phase 4 — optional polish

11. **"Currently tending" live indicator.** ✅ Worker built. Client-side
    polls `https://tend-currently.maxsixty.workers.dev` (~30 s). Small dot
    + most-recent-action timestamp. UI session falls back to the most
    recent event in `activity.json` when the Worker response is empty or
    unreachable.

## Open questions (decide before phase 1)

- **Domain.** `tend.dev` is the natural sibling to `worktrunk.dev` — check
  availability. Fallbacks: `tend.bot`, `tend.maintainer.dev`. Affects copy
  and OG metadata.
- **Sibling closeness.** Same worktrunk theme retinted (fast, reads as a
  visual family) vs. distinct visual identity (longer, more memorable).
  Recommend: start retinted, decide whether to diverge after phase 2.
- **Headings font.** Inter/Jakarta (matches worktrunk) vs. humanist serif
  (Newsreader / Source Serif, pushes toward "field notes"). Decide in
  session #2.
- **Screenshot sourcing.** Self-host from tend's own repo, or seek consent
  from third-party adopters? Self-host is the simpler default.

## Out of scope (for now)

- Marketplace / discoverability page for repos using the badge.
- Animated demos / Lottie scenes.
- Comparison tables vs. other agent tools.
- Blog or changelog (the GitHub releases page is enough).
