---
name: propose-instructions
description: Propose durable instructions. Use when a maintainer corrects the bot, or before writing text for a skill or an instruction file.
metadata:
  internal: true
---

# Proposing tend instructions from CI

Turning a maintainer's correction into durable instructions for future runs. It
lands in one of two places: this repo's own `running-tend` overlay, which only
sessions here read, or tend's bundled skills upstream at `max-sixty/tend`,
which every repo tend maintains reads. This skill covers whether the correction
clears the bar, which of the two it lands in, what the text carries, and the
mechanics of proposing it.

## Required skills

- Load `/tend-ci-runner:act-in-other-repos` before filing an upstream Tend issue.
- Load `/tend-ci-runner:post-to-github` and `/tend-ci-runner:open-pr` before composing and opening a repo-local skill PR.
- Load `/tend-ci-runner:push-commits` before pushing that PR.

## Whether to propose

Propose instructions when the evidence identifies a generalizable guidance
gap and the expected benefit outweighs the ambiguity, reading cost, and upkeep
of the rule. Inspect the guidance loaded at the decision and how it contributed
to the outcome. Repeated reports of a lapse do not establish that instructions
were missing or wrong; a failure under adequate guidance may need no new rule.

Weigh exposure, consequences, and what will catch the failure in future runs.
A failure invisible to CI can warrant an instruction repair without waiting
for another incident. Prefer correcting or removing the responsible text to
adding reminders beside adequate guidance. Task-specific details and uncertain
causal explanations stay in the current investigation.

A maintainer's request to codify a rule establishes their desired policy even
without prior incidents. Non-maintainer feedback can supply evidence, but only
a maintainer authorizes repository preferences — check `author_association`
before turning one into a durable rule.

## Where it lands

Settle the destination before drafting a line, on two questions: **would every
tend consumer want this rule**, and **is it ours to state?** Mechanics are the
first kind — how GitHub behaves, what the harness does, what a session must
never do. A judgment about what is worth doing in a repo — the complexity it
will spend, the CI time worth saving, how many open PRs is too many — belongs
to whoever maintains that repo, and goes in its overlay even where it reads as
generic good sense; bundled, it makes every other repo answer to this one's
maintainer.

Where the answer to both is yes, the rule belongs in tend's bundled skills, and
what the bundled text currently says doesn't change that:

- **Bundled text is wrong or unclear** — fix it upstream.
- **Bundled text is silent** — the same gap, usually a wider one, since
  nothing is enforcing the rule for any consumer. Silence is not evidence the
  rule is local; it is the commonest reason a maintainer had to correct
  generic behavior in the first place.
- **The rule is already merged upstream, but not in the pinned release** — the
  fix is a release, not a local copy. The lag is temporary and overlay text is
  permanent: once the release ships, every consumer that forked the rule
  carries a duplicate someone has to notice and delete, and until then the two
  copies drift.

The overlay is for what is true of one repo alone — its branch and landing
conventions, its test topology, its trackers and labels, its standing
exceptions — and for its maintainer's judgments above. A rule of mechanics goes
upstream even when writing it locally would be quicker.

Upstream means an issue on `max-sixty/tend`, filed per **Filing issues** in
`/tend-ci-runner:act-in-other-repos`.

## What the text carries

Instructions state the rule a future session acts on: the shape to follow and
the shape to avoid, in a few lines. That holds in a skill, an overlay, or a
project instruction file such as `CLAUDE.md`, and for text suggested in a
review as much as text you commit. Leave out how the rule came about — the PRs,
issues, and runs that exposed the gap, and the maintainer's wording; the PR
body links that evidence. Cited cases read as the rule's targets and age into
trivia. A decision settled for one site belongs in a comment at that site,
where the next session to edit it will read it.

State what the session must achieve rather than how a harness gets it there:
"don't end your turn while anything the deliverable needs is still running",
not which tool calls run in the background or what kills them at exit.
Harnesses change those mechanics between releases, and a session reads its own
tools' descriptions. Name a mechanism only where a session would act wrongly
without it and its tools don't say.

When a skill's code block needs tested logic or edge-case handling, put it in
a tested script in a `scripts/` directory beside the skill, and leave the skill
a one-line invocation with the intent. A prose
recipe gets no shellcheck and no tests; every session re-derives its
correctness.

## Mechanics

These steps are for the overlay path. Filing upstream follows
`/tend-ci-runner:act-in-other-repos` instead.

1. **Complete the current task first.** The skill update is always a separate
   PR.

2. **Check for an existing open PR against the same skill.** Dedup by the
   target file, not by title — title conventions vary per repo:

   ```bash
   BOT_LOGIN=$(gh api user --jq '.login')
   gh pr list --state open --author "$BOT_LOGIN" --limit 200 --json number,title,headRefName,files \
     --jq '.[] | select([.files[].path] | index(".claude/skills/running-tend/SKILL.md"))'
   ```

   If one is open, add to it instead of opening a second.

3. **Draft the edit** per **What the text carries**, under an appropriate
   heading. New SKILL.md files start with YAML frontmatter:

   ```markdown
   ---
   name: running-tend
   description: Project-specific instructions for tend workflows running on this repo.
   ---
   ```

   Do the edit, commit, and push from a git worktree under `$TMPDIR`.

   Base the skill branch on the repo's default branch, **not `HEAD`**. When
   this runs from `tend-mention` on a PR, the workflow has already done
   `gh pr checkout` so `HEAD` is the PR branch — basing on it carries that
   PR's WIP commits into the skill PR and ships a multi-concern PR that mixes
   the skill change with unrelated code. Fetch and base off
   `origin/<default>` instead:

   ```bash
   DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')
   git fetch origin "$DEFAULT_BRANCH"
   git worktree add "$TMPDIR/skill-fix" -b "skills/<topic>-$GITHUB_RUN_ID" "origin/$DEFAULT_BRANCH"

   # Write the skill file in the worktree, at
   # $TMPDIR/skill-fix/.claude/skills/running-tend/SKILL.md. The `cd` below
   # stays inside a subshell, so the session's own cwd never enters the
   # worktree: the last line deletes it, and a session standing in it has no
   # working directory for anything after this block.

   (
     set -e
     cd "$TMPDIR/skill-fix"
     git add .claude/skills/
     git commit -m "skills(running-tend): ..."
     git push -u origin skills/<topic>-$GITHUB_RUN_ID
     gh pr create --title "..." --body-file "$TMPDIR/pr-body.md" --head skills/<topic>-$GITHUB_RUN_ID
   ) && git worktree remove "$TMPDIR/skill-fix" --force
   ```

4. **Open as a separate PR.** Follow the repo's PR title conventions
   (conventional commits, Jira prefix, or whatever the repo uses — check
   recent merged PRs or `CONTRIBUTING.md`). The body states the generalized
   behavior gap and the outcome the new instructions should produce, then links
   the triggering thread as evidence. Do not quote or reconstruct the exchange.

5. **Open and exit — don't merge, don't wait.** The PR itself is the review
   request; a maintainer lands it (or doesn't) in their own time. Don't post
   a separate comment pinging for review, and don't block the session
   waiting. This open-and-exit is for skill proposals only; a code fix
   follows **Complete the task before ending the turn** in
   `/tend-ci-runner:run-tend`.
