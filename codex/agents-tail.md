## Skills (Codex-specific)

The `tend-ci-runner` plugin is installed; its skills (`review`,
`triage`, `ci-fix`, `nightly`, `weekly`, `notifications`,
`review-runs`, `running-in-ci`, `review-reviewers`) are invocable
via `$<skill-name>` mentions in prompts.

ALWAYS load `$running-in-ci` first when handling any workflow — it
covers CI security rules, polling conventions, and comment-formatting
guidance. Other skills depend on it.

Repo-local skills live under `.claude/skills/<name>/SKILL.md` in the
adopter's repo (e.g. `running-tend`). The `running-in-ci` skill tells
you when to load them.

## Tooling

- `gh` is authenticated as the bot via `$GH_TOKEN`. Use it to post
  comments, open PRs, push commits.
- `uvx` is on PATH; use it for one-shot Python tools.
- The bot's user ID is available via `gh api users/${BOT_NAME} --jq .id`
  if you need it for `author.id` comparisons.
