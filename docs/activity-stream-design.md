# Activity stream design

`/activity` is the "what tend has *done*" stream on the marketing site — recent
PRs tend opened and merged, reviews and comments it left, commits it pushed,
issues it closed, dependency bumps it cleared. It's built from each bot's public
event timeline plus one merged-PR search; `docs/website-data.md` documents the
live shape, kinds, and sources. This doc records why that design, what it
deliberately can't show, and what's still open.

## Decision: per-actor events API

`/activity` fans out `GET /users/<bot>/events/public` (one cheap REST call per
bot, already discriminated by event type) plus `author:<bot> is:pr is:merged`
(one Search call per bot — the merge is usually performed by a human, so it
isn't in the bot's own event stream). Rows are merged across bots, comments and
pushes to the same PR/branch collapse with a `count`, sorted newest-first,
capped at 40.

This replaced the original `/activity`, which ran three Search queries per bot
(`author:<bot> is:pr` → `ci-fix`, `commenter:<bot> is:pr -author:<bot>` →
`review`, `commenter:<bot> is:issue` → `triage`). That feed was keyed on
*issue/PR touched*, not *action taken*, so it collapsed distinct things — a CI
fix, a nightly conflict resolution, a weekly dependency bump, a workflow
self-edit were all "ci fix"; merged-or-not was invisible; dependency-PR
approvals (authored by `dependabot[bot]`, not the bot) barely registered; issue
closes and pushed commits didn't appear at all.

Alternatives considered and rejected:

- **More Search queries (extend the original).** Search bursts (kinds × N
  concurrent requests) against GitHub's 30/min Search cap; the original 3-kind
  feed already neared that around N≈10, and a richer one would hit it sooner.
  Multiple `author:` qualifiers AND to nothing, so the per-bot fanout can't be
  collapsed. The events API moves the bulk onto the 5,000/hr REST budget
  instead, raising the ceiling.
- **Per-repo events / issue timeline (`GET /repos/<repo>/events`).** Same cost
  as the events API but a busy repo's 30 most-recent events can contain zero bot
  events, forcing pagination. The per-actor stream doesn't have that problem —
  every event in it is the bot's.
- **tend emits its own activity log.** Each workflow run appends a one-line
  record of what it did, in tend's own words ("fixed flaky test: race in cache
  TTL — #441"). The only source that captures *content*, not just titles and
  URLs — but it needs a home (a long-lived "tend log" issue per consumer repo, a
  Worker write endpoint, …), all of which add a visible artifact or new surface.
  Deferred; the natural next step if breadth (more kinds) isn't enough and
  people want depth (tend's own summaries).

## What the feed still can't show

Some tend behaviours aren't reliably attributable from public APIs:

- "resolved a merge conflict" vs. "pushed a normal fix" — both are a `PushEvent`
  with a merge commit; the event doesn't say the merge had conflicts. The feed
  shows both as `pr-commits`.
- "swept these 4 files and found nothing" — a nightly survey that produced no PR
  leaves no trace.
- *what* a review actually said, in tend's words — the API gives the comment
  body, not a one-line summary. (Only the tend-emitted log would.)

## Cost & headroom

Per `/activity` refresh: N REST (events) + N Search (merged) requests, paid once
per 5-min cache window — worst case 12 refreshes/hr.

| | per refresh | per hour | Search burst | headroom |
| --- | --- | --- | --- | --- |
| N=5 | 5 REST + 5 Search | ≤60 REST + ≤60 Search | 5 / 30 | comfortable |
| N=20 | 20 REST + 20 Search | ≤240 REST + ≤240 Search | 20 / 30 | comfortable |
| N=40 | 40 REST + 40 Search | ≤480 REST + ≤480 Search | 40 / 30 | over Search burst — drop the merge-Search, or move the route to a scheduled refresh |

`/currently-tending` (N REST every 30 s → 120N REST/hr) hits the 5,000/hr REST
budget around N≈40 on its own, so N≈40 is a cliff for the *whole* system, not
this route, and crossing it means a scheduled-refresh rearchitecture regardless.

`/stats` is the nearer problem: 5 Search counters × N concurrent reaches the
30/min cap at N=6 and goes over at N=7 — past which queries get 429'd and the
Worker degrades them to zero, undercounting silently. Fix independent of this
route: serialise the per-bot calls, or move `/stats` to a Worker cron refresh
into KV (a Cloudflare `scheduled` handler running the fanout every ~30 min,
serialised; the fetch handler just reads KV — the escape hatch for any route
that outgrows a cheap single fanout).

## Still open

- **Where it lives.** Today it's the hero-adjacent `Activity.astro` section,
  rendering the new kinds and showing the first 12 rows (the Worker returns up
  to 40). A dedicated `/activity` page is cheap (a second Astro route → GitHub
  Pages) and would have room for a day-grouped digest header ("Mon — tend opened
  3 PRs (2 merged), reviewed 5, closed 1 issue, cleared 2 dependency bumps"),
  the full feed, and a repo filter. Build it when there's appetite.
- **Per-kind styling.** `Activity.astro` tags each row with a `kind-<kind>` class
  but the CSS only styles `.kind` generically — distinct chip colours/glyphs per
  kind (merged green, changes-requested amber, …) are an easy follow-up.
- **Digest cadence.** If a digest happens — daily rollups (changelog-like) or a
  single "this week" line (one cheap `total_count` query set)?
- **`/stats` Search burst.** Fold the serialise-or-cron fix into this work, or
  track it separately? It's a today-problem at N=6.
- **`consumers.json` additions.** A repo display name would let the feed show
  "worktrunk" not "max-sixty/worktrunk". A tend-log issue number per repo would
  be needed if the tend-emitted-log option is ever pursued.
