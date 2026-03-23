# Continuous

> **Early development** — extracted from [worktrunk](https://github.com/max-sixty/worktrunk)'s CI automation. Expect breaking changes.

Claude-powered CI for GitHub repos. PR review, issue triage, @bot mentions,
CI fixes, nightly sweeps, dependency updates.

## How it works

Three pieces:

1. **Composite action** (`max-sixty/continuous@v1`) — installs generic skills,
   resolves bot ID at runtime, runs Claude Code, uploads session logs. The
   stable interface.

2. **Generator** (`uvx continuous init`) — stamps out workflow files into the
   adopter's `.github/workflows/`. Handles triggers, conditions, engagement
   verification, checkout. Idempotent — always overwrites from config.

3. **Config** (`.config/continuous.toml`) — bot identity, secret names, project
   setup steps. Only overrides from defaults are needed.

## Quick start

1. Create a bot GitHub account with a PAT (`contents:write`,
   `pull-requests:write`, `issues:write`).

2. Add repo secrets: `BOT_TOKEN` (the PAT) and `CLAUDE_CODE_OAUTH_TOKEN`.

3. Set up merge protection — the bot must not be able to merge PRs.

4. Add `.config/continuous.toml`:

   ```toml
   bot_name = "my-bot"

   [secrets]
   bot_token = "BOT_TOKEN"
   claude_token = "CLAUDE_CODE_OAUTH_TOKEN"
   ```

5. Generate and commit:

   ```bash
   uvx continuous init
   git add .github/workflows/continuous-*.yaml
   git commit -m "Add continuous workflows"
   ```

## Customization

Override defaults in `.config/continuous.toml`:

```toml
[setup]
uses = ["./.github/actions/my-setup"]
run = ["echo FOO=bar >> $GITHUB_ENV"]

[workflows.ci-fix]
watched_workflows = ["ci", "build"]

[workflows.nightly]
cron = "0 8 * * *"

[workflows.renovate]
enabled = false
```

## What's generated

| Workflow | Trigger |
|---|---|
| `continuous-review` | PR opened/updated, review submitted |
| `continuous-mention` | @bot mentions, engaged conversations |
| `continuous-triage` | Issue opened |
| `continuous-ci-fix` | CI fails on default branch |
| `continuous-nightly` | Daily schedule |
| `continuous-renovate` | Weekly schedule |

## Architecture

```
continuous/
├── action.yaml       # Composite action (the interface)
├── skills/           # Generic CI skills for Claude
├── scripts/          # Helper scripts (survey, run listing)
├── generator/        # Python package (uvx continuous)
└── docs/
    └── security-model.md
```

Project-specific behavior (test commands, review criteria, labels) stays in the
adopter's repo as skill overlays that reference the generic `continuous-*`
skills.

## Security

See [docs/security-model.md](docs/security-model.md).

## License

MIT
