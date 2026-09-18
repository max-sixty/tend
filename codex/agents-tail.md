## Skills (Codex-specific)

The `tend-ci-runner` and `install-tend` plugins are installed. Every
skill they carry is listed in your available skills and is invocable via a
`$<skill-name>` mention in a prompt — the workflow skills (`review`,
`triage`, `nightly`, and the rest), the per-action skills `run-tend` points
you at, and the diagnostic `/install-tend:debug-tend-run`.

**Read each tend skill in full.** When you open a `tend-ci-runner`
`SKILL.md`, read the entire file with `cat`. Do not read a prefix with
`sed -n '1,Np'` or `head`. These skills are short, and their trailing
sections carry load-bearing security, dedup, and CI-polling rules. A
prefix read silently drops those and produces wrong behavior. This
overrides any general "read only enough" instruction for tend skills.

`tend-ci-runner` carries one skill per action — posting, pushing,
opening a PR — and `review` keeps `references/` files for the cases only it
hits. Work out which your task will hit and read them as you plan the task,
in full, with `cat`.

Repo-local skills live under `.claude/skills/<name>/SKILL.md` in the
consumer's repo (e.g. `running-tend`). The `run-tend` skill tells
you when to read them; read those in full too.

## Tooling

- `gh` is authenticated as the bot via `$GH_TOKEN`. Use it to post
  comments, open PRs, push commits.
- `uv` is on PATH for Python environments and one-shot tools.
- The bot's user ID is available via `gh api users/${BOT_NAME} --jq .id`
  if you need it for `author.id` comparisons.
