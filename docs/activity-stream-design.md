# Activity stream design

`/activity` reports recent activity in **primitive buckets** — `prs` (PRs the
bot opened), `issues` (issues the bot opened), `comments` (PRs/issues the bot
chimed in on) — each with a lifetime `count`, a `count_this_week`, and a short
`recent` list. `docs/website-data.md` has the live shape and the queries; this
note records why it's shaped that way.

## Why primitive buckets, not a job taxonomy

GitHub gives us *mechanical* facts — a PR was opened, a review was submitted, a
comment was created — but tend's *jobs* (reviewing PRs, triaging issues, fixing
CI, nightly/weekly maintenance, dependency bumps) don't map onto them cleanly:
a PR on `fix/ci-*` vs `fix/issue-*` vs `tend/update-workflows` is the same event
type, and some jobs (a nightly survey that finds nothing) leave no trace at all.
An earlier cut tried to reverse-engineer the jobs into a `kind` enum
(`ci-fix`/`review`/`triage`, then a richer `pr-opened`/`pr-merged`/… set) — every
version needed branch-name heuristics and still mislabelled things.

So: don't reverse-engineer. Report the mechanical buckets honestly, cheaply
(one Search query per bucket per bot — the page gives the recent items *and* the
count), and leave the "what's tend been up to" narrative to a later layer.

## Phase 2: an LLM summary

The buckets are the *data*; the *narrative* — a short prose "here's what tend's
been doing" — is deferred to a consumer that reads `/activity` and writes a
summary into KV (a scheduled job, or the Worker calling Claude). That's tracked
as the TODO in `docs/website-data.md`. Until it exists, the site renders the
buckets directly (a stat strip from the counts, a recent feed from the `recent`
lists).

## Considered and dropped

- **A per-event `/activity` feed with a `kind` taxonomy** (events API +
  branch-name categories + collapsing) — the mapping was the problem, see above.
- **Aggregate-only `/stats`** — folded into `/activity` (its `count` /
  `count_this_week`); one fewer route, one fewer fetch from the site.
- **A KV/D1 accumulator that appends activity as it arrives** — only worth it if
  the Phase-2 summary needs a span longer than GitHub's ~90-day events window or
  one Search page; demand-fetch is cheap enough until then.
