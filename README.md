# Continuous

> **Early development** — this project is being extracted from [worktrunk](https://github.com/max-sixty/worktrunk)'s CI automation. It's not ready for use yet. Expect breaking changes to inputs, skill names, and workflow structure.

Claude-powered CI workflows for GitHub repositories. Provides automated PR review, issue triage, @bot mentions, CI fixes, nightly code sweeps, and dependency updates.

## Quick start

1. Create a bot GitHub account (or use an existing one) and generate a PAT with `contents:write`, `pull-requests:write`, `issues:write` permissions.

2. Add two secrets to your repo:
   - `BOT_TOKEN` — the bot's PAT
   - `CLAUDE_CODE_OAUTH_TOKEN` — your Claude OAuth token

3. Set up merge protection — the bot should not be able to merge PRs. Use a ruleset or branch protection to restrict merges to admins.

4. Copy the [template workflows](templates/.github/workflows/) to your repo's `.github/workflows/` and update the bot name, bot ID, and secret names.

## What's included

| Workflow | Trigger | What it does |
|---|---|---|
| `review` | PR opened/updated, review submitted | Automated code review with inline suggestions |
| `triage` | Issue opened | Classifies, reproduces bugs, attempts fixes |
| `mention-comment` | Comment on issue/PR | Responds to @bot mentions and engaged conversations |
| `mention-review` | Inline review comment | Responds to inline review comments on PRs |
| `mention-issue-edit` | Issue edited with @bot | Responds to @bot mentions added via edit |
| `ci-fix` | CI fails on default branch | Diagnoses and fixes CI failures |
| `nightly` | Scheduled (daily) | Resolves conflicts, reviews commits, surveys code |

## Architecture

```
continuous/
├── .github/workflows/     # Reusable workflows (workflow_call)
├── action.yaml            # Composite action: installs skills + scripts
├── skills/                # Generic CI skills for Claude
├── scripts/               # Helper scripts (survey, run listing)
├── docs/                  # Security model, configuration reference
└── templates/             # Starter caller workflows for adopters
```

Adopting repos call the reusable workflows with `uses: max-sixty/continuous/.github/workflows/review.yaml@main`. The composite action installs generic skills into `.claude/skills/` at runtime.

Project-specific behavior (language-specific review criteria, test commands, labels) stays in the adopter's repo as skill overlays that reference the generic `cd-*` skills.

## Security model

See [docs/security-model.md](docs/security-model.md) for the full security model covering merge restrictions, token management, prompt injection, and secret exfiltration prevention.

## License

MIT
