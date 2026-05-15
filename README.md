<h1><img src="assets/logo-512.png" alt="tend logo" width="50" align="absmiddle">Tend</h1>

[![PyPI](https://img.shields.io/pypi/v/tend?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/tend/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![CI](https://img.shields.io/github/actions/workflow/status/max-sixty/tend/ci.yaml?event=push&branch=main&style=for-the-badge&logo=github)](https://github.com/max-sixty/tend/actions?query=branch%3Amain+workflow%3Aci)
[![maintained with tend](https://img.shields.io/badge/maintained_with-tend-bba580?style=for-the-badge&logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwxNikgc2NhbGUoMC4wMTI1LC0wLjAxMjUpIiBmaWxsPSIjZmZmIiBzdHJva2U9Im5vbmUiPjxwYXRoIGQ9Ik02ODAgMTEyOCBjNjIgLTk2IDY5IC0xNzggMjAgLTI0MSAtMTcgLTIyIC0yMCAtNDAgLTIwIC0xMzQgbDEgLTEwOCAyMSAyOCBjMTEgMTYgMzAgNDcgNDIgNzAgMTIgMjIgMzIgNDkgNDYgNTkgMzcgMjcgMTE0IDM4IDE4NCAyNyA5MyAtMTUgOTQgLTE4IDQ0IC03OSAtNzIgLTg4IC0xMDkgLTExMyAtMTc2IC0xMTcgLTMxIC0yIC02NCAxIC03MiA2IC0yMyAxNSAyMSA1NiAxMDcgOTggNDAgMjAgNzEgMzggNjkgNDAgLTYgNyAtODggLTE3IC0xMjYgLTM3IC00OSAtMjUgLTEwMCAtNzggLTEyMSAtMTI1IC0xNSAtMzMgLTE5IC02NiAtMTkgLTE4OCAwIC0xNTcgOCAtMTk1IDUwIC0yMzIgMTcgLTE2IDM2IC0yMCA4NSAtMTkgNjIgMSA2MyAxIDczIC0zMiA5IC0zMiA5IC0zMyAtMjIgLTQwIC01MCAtMTIgLTEzMiAtNyAtMTY0IDEwIC00MCAyMSAtNzkgNjkgLTkyIDExNCAtNSAyMCAtMTAgMTAyIC0xMCAxODIgMCA4MCAtNSAxNjIgLTExIDE4NCAtMjIgNzkgLTEzNSAxNjYgLTIzNCAxODEgLTM3IDYgLTM1IDMgMzAgLTI4IDc4IC0zOSAxNDQgLTkxIDEzMiAtMTA0IC01IC00IC0zNyAtOCAtNzEgLTggLTc3IDAgLTExNyAyNCAtMTgyIDEwOSAtNTIgNjggLTUxIDcwIDQyIDg1IDcxIDExIDE0MyAwIDE4MyAtMjkgMTYgLTExIDQwIC00MyA1NCAtNzMgMTMgLTI5IDMyIC01OSA0MSAtNjYgMTQgLTEyIDE2IC03IDE2IDU4IDAgNTkgNCA3NyAyMyAxMDIgMTkgMjYgMjMgNDYgMjUgMTMwIDMgNjcgMCA5OSAtNyA5OSAtNyAwIC0xMSAtMjMgLTEyIC01NyAwIC0zMiAtNiAtNzYgLTEyIC05NyBsLTEyIC00MCAtMjcgMzIgYy0zNCA0MSAtNDMgOTYgLTI0IDE1MSAxNCA0MSA3NSAxNDEgODYgMTQxIDMgMCAyMSAtMjQgNDAgLTUyeiIvPjwvZz48L3N2Zz4K)](https://github.com/max-sixty/tend)

<!-- [![Stars](https://img.shields.io/github/stars/max-sixty/tend?style=for-the-badge&logo=github)](https://github.com/max-sixty/tend/stargazers) -->

Tend allows open-source projects to have an agent as a dutiful junior
maintainer. The agent can review PRs, triage issues, fix CI, help out with
research, maintain a changelog, sweep the repo for improvements, refine
documentation, etc.

> Current status: Tend is in its early days. It has been working _extremely_ well in
> [Worktrunk](https://www.github.com/max-sixty/worktrunk) for the past couple of
> months, such that folks suggested I generalize it into its own project.

## Structure

To use Tend, a project needs:

- A GitHub account for the agent (for example this project's is **[@tend-agent](https://www.github.com/tend-agent))**
- One of:
  - A Claude Max subscription (harness = "claude")
  - An OpenAI API key, or a ChatGPT subscription via a Codex `auth.json` (harness = "codex")

Tend offers the default code & guidance for the agent. Specifically that means:

- A set of workflow templates
- A very particular set of Skills
  - ...skills it has acquired over a very long career (two months)

Each project's agent remains completely under its control, and runs only in the
project's Github Actions environment. The Tend project never sees any tokens /
keys / etc.

<!-- TODO: add screenshot of a review comment, triage response, or CI fix PR -->

## Quick start

The easiest way to get started is to install the Tend plugin into a local Claude
Code session, and run the [`/install-tend` skill](plugins/install-tend/skills/install-tend/SKILL.md):

```sh
claude plugin marketplace add max-sixty/tend
claude plugin install install-tend@tend
claude /install-tend
```

It'll take 5-15 minutes to set up the config, workflow generation, bot account,
secrets, and branch protection. Tend is configured through a [config
file](docs/tend.example.yaml) and a repo-local `/running-tend` skill.

## Reasons _not_ to use Tend

- Tend uses lots of tokens. Either a Claude Max subscription, an OpenAI API
  key, or a ChatGPT plan is needed to fund the runs.
  - Maintainers of sizeable OSS projects [get a 20x Claude Max subscription
    for free from
    Anthropic](https://claude.com/contact-sales/claude-for-oss).
  - Anthropic has restricted OAuth tokens from Free/Pro/Max plans to Claude
    Code and claude.ai only — using Claude Max with `claude-code-action`
    in CI is in a grey zone and may be enforced against. The Codex harness
    is an alternative.
- While it's built to protect important secrets, a determined attacker can
  get a) the bot's token and b) the harness auth credential (Claude OAuth
  token, OpenAI API key, or ChatGPT auth.json). They can't do that much
  with these: burn some tokens and close some issues.
  - They specifically _cannot_ merge to the default branch, nor create releases.

## Workflows

| Workflow          | Trigger                    | What happens                                                                                                                                                |
| ----------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **review**        | PR opened/updated          | Reviews for correctness and duplication. Traces error paths. Monitors CI. Pushes mechanical fixes to bot-authored PRs.                                      |
| **mention**       | @bot mention, review       | Responds to requests in PR and issue conversations.                                                                                                         |
| **triage**        | Issue opened               | Classifies the issue, checks for duplicates, reproduces bugs, attempts conservative fixes.                                                                  |
| **ci-fix**        | CI fails on default branch | Reads failure logs, identifies root cause, searches for the same pattern elsewhere, opens a fix PR.                                                         |
| **nightly**       | Daily                      | Resolves conflicts on open PRs, reviews recent commits, surveys ~10 files for bugs and stale docs, closes resolved issues, regenerates tend workflow files. |
| **weekly**        | Weekly                     | Reviews dependency PRs, auto-merges safe patch and minor updates.                                                                                           |
| **notifications** | Every 15 minutes           | Polls GitHub notifications, responds to unhandled mentions, marks handled threads as read.                                                                  |
| **review-runs**   | Daily                      | Reviews recent CI runs for behavioral problems and proposes skill/config improvements.                                                                      |

Scheduled workflows also support manual dispatch for testing. All are
enabled by default except **ci-fix**, which requires `watched_workflows`
to be configured. Any can be disabled:

```yaml
workflows:
  weekly:
    enabled: false
```

## How it works

`uvx tend@latest init` reads `.config/tend.yaml` and writes `tend-*.yaml` workflow
files into `.github/workflows/`. Each workflow handles triggers, skip
conditions, concurrency, and permissions — then calls the composite action
for the configured harness (`max-sixty/tend@v1` for Claude,
`max-sixty/tend/codex@v1` for Codex).

Both actions run the same security and rate-limit preflight checks and
resolve bot identity; they differ only in which model runs the prompt:

- **Claude harness** — invokes
  [claude-code-action](https://github.com/anthropics/claude-code-action)
  with the tend plugin. Each workflow's prompt is a slash command
  (`/tend-ci-runner:review`) that loads the matching skill.
- **Codex harness** — installs the `@openai/codex` CLI on the runner and
  shells out to `codex exec`. An AGENTS.md staged into `$CODEX_HOME`
  teaches Codex to resolve `/tend-ci-runner:NAME` references to the
  bundled skill markdown.

Edit the config or the generator — not the workflow files. They're regenerated
on every `tend@latest init`.

## Security

Tend gives Claude write access to a repository. The security model has four
layers:

**Merge restriction** is the primary boundary. A GitHub ruleset prevents the
bot from merging to protected branches — bot-authored PRs require human
approval. `tend check` verifies this; `tend check --fix` creates the ruleset.

**Config pinning** — `claude-code-action` restores `.claude/`, `.mcp.json`,
`.claude.json`, `.gitmodules`, and `.ripgreprc` from the base branch on all
PRs (preventing startup-time code execution). Tend additionally pins
`CLAUDE.md` on fork PRs to block prompt injection from untrusted sources.

**Rate limiting** — Burst detection (10 PRs and 10 issues per 20 minutes,
checked independently) and daily spike detection halt the bot before runaway
loops cause damage.

**Fixed prompts** — Workflow prompts come from the action, not from
attacker-controlled input like PR descriptions or comments.

Full threat model: [docs/security-model.md](docs/security-model.md).

## Configuration

`.config/tend.yaml` — only `bot_name` is required. The default harness is
Claude; set `harness: codex` to use OpenAI Codex instead.

```yaml
bot_name: my-project-bot

# Optional — defaults to "claude"
# harness: codex
# effort: medium   # codex only: minimal | low | medium | high
```

Repo secrets depend on the harness:

| Harness   | Required secrets                                                                                                |
| -------- | --------------------------------------------------------------------------------------------------------------- |
| `claude` | `BOT_TOKEN` + one of `CLAUDE_CODE_OAUTH_TOKEN` (subscription, see caveat below) or `ANTHROPIC_API_KEY` (API-billed) |
| `codex`  | `BOT_TOKEN` + one of `CODEX_AUTH_JSON` (subscription, recommended) or `OPENAI_API_KEY` (pay-per-token) |

`BOT_TOKEN` is the bot account's PAT — see
[example config](docs/tend.example.yaml) for scopes.
`CLAUDE_CODE_OAUTH_TOKEN` is from `claude setup-token`;
`CODEX_AUTH_JSON` is the contents of the `auth.json` Codex writes after
`codex login --device-auth`. The other two are standard API keys from
console.anthropic.com and platform.openai.com. See
[Codex (alternative)](#codex-alternative) for the Codex trade-off and
public-repo gate; [docs/security-model.md](docs/security-model.md) has
the full leak breakdown.

All other options — secret name overrides, setup steps, protected branches,
workflow overrides, schedules — are documented in
[`docs/tend.example.yaml`](docs/tend.example.yaml).

## Project context

Tend reads `CLAUDE.md` like any Claude Code session — build commands, test
commands, project conventions all go there.

For tend-specific guidance, add a skill overlay at
`.claude/skills/running-tend/SKILL.md`. Common uses: recording which CI
workflow names `tend-ci-fix` watches, PR title conventions, label policies.

## Harnesses

Tend supports two harnesses. Pick whichever fits the credentials you
already have; both run with the same workflows and skills.

### Claude (default)

Wraps
[`claude-code-action`](https://github.com/anthropics/claude-code-action),
Anthropic's official GitHub Action for running Claude Code in CI. Two
auth modes:

- **`CLAUDE_CODE_OAUTH_TOKEN`** — Claude Code OAuth token from
  `claude setup-token`. Funded by a Max/Team subscription before
  2026-06-16; see caveat below.
- **`ANTHROPIC_API_KEY`** — standard API key from console.anthropic.com,
  billed per token. Works for any repo and is the recommended path for
  open-source.

Whichever you set is used exactly as it would be in any
claude-code-action workflow — tend adds the framework (workflows, skills,
prompts) around it but never sees the token itself.

Caveat: starting **2026-06-16**, Anthropic bills non-interactive Claude
Code runs (CI, headless, batch) at API rates rather than against
Max/Pro/Team plan allowances. `anthropics/claude-code-action` in
GitHub Actions is non-interactive — a Max subscription will not fund
runs after that date. Adopters needing subscription-funded CI should
plan to move to the Codex harness, or switch to `ANTHROPIC_API_KEY`. See
Anthropic's
[authentication policy](https://code.claude.com/docs/en/authentication)
for the canonical statement.

### Codex (alternative)

Installs `@openai/codex` on the runner and invokes `codex exec` against a
bundled `AGENTS.md` that teaches it to resolve tend's slash commands to
skill markdown. Two auth modes:

- **`CODEX_AUTH_JSON`** (recommended) — Codex `auth.json` shipped
  as a secret, billed at the Plus/Pro/Business subscription's flat
  rate. On public repos the token must come from a ChatGPT account
  dedicated to the bot; recommended on private. See
  [docs/security-model.md](docs/security-model.md) for the leak
  breakdown and rotation cadence.
- **`OPENAI_API_KEY`** — pay-per-token API billing. Works for any
  repo; pick this to avoid a separate ChatGPT account.

## Badge

A badge signals the repo is maintained with tend:

```markdown
[![maintained with tend](https://img.shields.io/badge/maintained_with-tend-bba580?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwxNikgc2NhbGUoMC4wMTI1LC0wLjAxMjUpIiBmaWxsPSIjZmZmIiBzdHJva2U9Im5vbmUiPjxwYXRoIGQ9Ik02ODAgMTEyOCBjNjIgLTk2IDY5IC0xNzggMjAgLTI0MSAtMTcgLTIyIC0yMCAtNDAgLTIwIC0xMzQgbDEgLTEwOCAyMSAyOCBjMTEgMTYgMzAgNDcgNDIgNzAgMTIgMjIgMzIgNDkgNDYgNTkgMzcgMjcgMTE0IDM4IDE4NCAyNyA5MyAtMTUgOTQgLTE4IDQ0IC03OSAtNzIgLTg4IC0xMDkgLTExMyAtMTc2IC0xMTcgLTMxIC0yIC02NCAxIC03MiA2IC0yMyAxNSAyMSA1NiAxMDcgOTggNDAgMjAgNzEgMzggNjkgNDAgLTYgNyAtODggLTE3IC0xMjYgLTM3IC00OSAtMjUgLTEwMCAtNzggLTEyMSAtMTI1IC0xNSAtMzMgLTE5IC02NiAtMTkgLTE4OCAwIC0xNTcgOCAtMTk1IDUwIC0yMzIgMTcgLTE2IDM2IC0yMCA4NSAtMTkgNjIgMSA2MyAxIDczIC0zMiA5IC0zMiA5IC0zMyAtMjIgLTQwIC01MCAtMTIgLTEzMiAtNyAtMTY0IDEwIC00MCAyMSAtNzkgNjkgLTkyIDExNCAtNSAyMCAtMTAgMTAyIC0xMCAxODIgMCA4MCAtNSAxNjIgLTExIDE4NCAtMjIgNzkgLTEzNSAxNjYgLTIzNCAxODEgLTM3IDYgLTM1IDMgMzAgLTI4IDc4IC0zOSAxNDQgLTkxIDEzMiAtMTA0IC01IC00IC0zNyAtOCAtNzEgLTggLTc3IDAgLTExNyAyNCAtMTgyIDEwOSAtNTIgNjggLTUxIDcwIDQyIDg1IDcxIDExIDE0MyAwIDE4MyAtMjkgMTYgLTExIDQwIC00MyA1NCAtNzMgMTMgLTI5IDMyIC01OSA0MSAtNjYgMTQgLTEyIDE2IC03IDE2IDU4IDAgNTkgNCA3NyAyMyAxMDIgMTkgMjYgMjMgNDYgMjUgMTMwIDMgNjcgMCA5OSAtNyA5OSAtNyAwIC0xMSAtMjMgLTEyIC01NyAwIC0zMiAtNiAtNzYgLTEyIC05NyBsLTEyIC00MCAtMjcgMzIgYy0zNCA0MSAtNDMgOTYgLTI0IDE1MSAxNCA0MSA3NSAxNDEgODYgMTQxIDMgMCAyMSAtMjQgNDAgLTUyeiIvPjwvZz48L3N2Zz4K)](https://github.com/max-sixty/tend)
```

The install-tend skill offers to add this automatically during setup.

## License

MIT
