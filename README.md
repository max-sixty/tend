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
  - A ChatGPT Plus or Pro subscription (experimental), or an OpenAI API key
    (harness = "codex") — see
    [Codex (experimental alternative)](#codex-experimental-alternative).

Tend offers the default code & instructions for the agent. Specifically that means:

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

- Tend uses lots of tokens. A Claude subscription, an Anthropic API key,
  an OpenAI API key, or a ChatGPT plan is needed to fund the runs.
  - Maintainers of sizeable OSS projects [get a 20x Claude Max subscription
    for free from
    Anthropic](https://claude.com/contact-sales/claude-for-oss).
- A compromise of the runner or credential proxy could expose the bot PAT or
  long-lived model credential. The agent cannot read those during normal
  operation; subscription-mode Codex receives only an expiring access token.
  In the default `maintainer` merge mode, the merge restriction prevents a
  stolen bot credential from landing code. `yolo` deliberately gives that up
  for ordinary code while retaining maintainer ownership of Tend's workflows and
  config, admin-only tags and extra protected branches, and credential gates.

## Workflows

| Workflow          | Trigger                    | What happens                                                                                                                                                |
| ----------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **review**        | PR opened/updated          | Reviews for correctness and duplication. Traces error paths. Monitors CI. Pushes fixes to bot-authored PRs.                                                 |
| **mention**       | @bot mention, review       | Responds to requests in PR and issue conversations.                                                                                                         |
| **mention-relay** | PR review                  | Hands review events on same-repo PRs to **mention** from a job that holds no secrets.                                                                       |
| **triage**        | Issue opened               | Classifies the issue, checks for duplicates, reproduces bugs, attempts conservative fixes.                                                                  |
| **ci-fix**        | CI fails or is cancelled   | Diagnoses the unsuccessful default-branch run, searches for the same pattern elsewhere, and opens a fix PR when needed.                                  |
| **nightly**       | Daily                      | Resolves conflicts on open PRs, reviews recent commits, surveys ~10 files for bugs and stale docs, closes resolved issues, regenerates tend workflow files. |
| **weekly**        | Weekly                     | Reviews dependency PRs and approves safe patch and minor updates; merging follows the configured mode.                                                     |
| **notifications** | Every 15 minutes           | Drains unread notifications as a recovery queue and repairs conflicts on bot-authored PRs.                                                                  |
| **review-runs**   | Daily                      | Reviews recent CI runs for behavioral problems and proposes skill/config improvements.                                                                      |
| **codex-auth-refresh** | Weekly                 | When any workflow uses Codex, renews experimental Plus/Pro auth through its single-writer credential; no-ops for API-key installs.                           |

The bot reacts 👀 while a session is working: on an issue when it opens, on a
PR whenever a review starts, and on a comment that mentions the bot. The
reaction comes off when the session ends.

Scheduled workflows also support manual dispatch for testing. GitHub runs
`schedule` triggers on a best-effort basis and drops ticks under load, so
the intervals above are the requested cadence rather than a guarantee —
observed gaps between runs are routinely longer. All are generated by
default except **ci-fix**, which requires `watched_workflows` to be
configured. Any can be omitted on the next regeneration:

```yaml
workflows:
  weekly:
    enabled: false
```

A fork carries these workflows, schedules included. Where the fork has Actions
enabled, each scheduled tick adds a run to its Actions tab. The jobs in those
runs check the repository's owner and skip, so the runs use no runner time and
start no agent. GitHub turns a public fork's scheduled workflows off after 60
days without activity, and the fork's owner can turn them off sooner by
disabling its `tend-*` workflows (`gh workflow disable`).

## How it works

`uvx tend@latest init` reads `.config/tend.yaml` and writes `tend-*.yaml` workflow
files into `.github/workflows/`. Each workflow handles triggers, skip
conditions, concurrency, and permissions — then calls the composite action
for the configured harness, pinned to the released generator version
(`max-sixty/tend/claude@X.Y.Z` for Claude, `max-sixty/tend/codex@X.Y.Z` for Codex).
The nightly regen restamps a newer tag when a new tend version ships.

When the review workflow is generated, `init` also merges one ignore into
`.github/actionlint.yaml`: the workflow's `concurrency.queue` is valid GitHub
syntax that actionlint's schema rejects. The ignore applies only to generated
workflows and preserves the rest of the consumer-owned config.

Both actions run the same security and rate-limit preflight checks and
resolve bot identity. They differ in how the agent runs:

- **Claude harness** — runs the official `claude` binary headless
  (`claude -p`) as a non-sudo user inside a hardened systemd unit, behind a local
  credential-injecting proxy, so the bot token and Anthropic credential
  never enter the agent's environment. Each workflow's prompt is a slash
  command (`/tend-ci-runner:review`) that loads the matching skill.
- **Codex harness** — installs the `@openai/codex` CLI, then runs
  `codex exec` inside the same boundary. GitHub calls use Tend's
  exact-host proxy. API-key model calls use OpenAI's narrow Responses API
  proxy; subscription sessions receive only an expiring access token.
  An AGENTS.md staged into `$CODEX_HOME` teaches Codex to resolve
  `/tend-ci-runner:NAME` references to the bundled skill markdown.

Edit the config or the generator — not the workflow files. They're regenerated
on every `tend@latest init`.

## Security

Tend gives an agent write access to a repository and points it at input anyone
can write: pull requests, issues, comments. The design assumes a session can be
hijacked, and bounds what a hijacked session can do. Maintainer mode keeps it
from landing code; yolo intentionally permits ordinary code changes while
keeping the repository control plane behind a maintainer. Credential
isolation, the sandbox, and the environment gate keep the bot and model
credentials out of the agent process.

**Merge mode** is explicit. `maintainer` (the default) gives the write-access
bot no bypass of the default branch's update rule. `yolo` gives that bot a
pull-request-only bypass, so it can merge through GitHub but cannot push the
branch directly. A separate CODEOWNERS rule requires fresh maintainer approval for
changes to `.github/**`, `.config/tend.yaml`, CODEOWNERS, and agent instruction
files; additional protected branches and tags stay admin-only. Preflight asks
GitHub for the bot's own effective bypass and refuses to run unless it exactly
matches the configured policy. `tend check --fix` reconciles the rulesets.

Maintainer mode may use runner-side `setup`; yolo refuses it because ordinary
code the bot merged could steer even a fixed command before the hardened agent
unit starts. Yolo also refuses workflow and job overrides so secret-bearing
jobs retain their audited shape.

**Credential isolation** — the bot's GitHub token and the long-lived model
credential never enter the agent's process. Tend's proxy on the runner holds
the bot token and Claude's model credential, and adds each only to requests
bound for its exact host. API-key Codex auth goes through OpenAI's proxy, which
forwards only Responses API calls upstream. The agent holds placeholders,
except that subscription-mode Codex receives an expiring access-only token,
never the rotating refresh token. GitHub authentication applies to any
repository the bot account can access, including repositories other than the
one that started the run.

**Sandbox** — both harnesses run the event checkout and the whole agent turn,
including any build or test it runs from the event's code, as one process tree
inside a hardened systemd unit, under a separate non-sudo user. The system is
read-only, all network traffic goes through the credential proxy, and the
agent cannot gain privileges, create namespaces, or see other users' processes.
The proxy connects to any host but adds credentials only for GitHub and the
model API. The agent works in a copy-on-write view of the job's checkout and
home, so its writes never reach the job's own files, and the steps after it see
the tree that setup left. Its own home sits outside the view, and on a
self-hosted runner that home persists between jobs.

**Environment-gated credentials** — yolo deliberately lets code the bot merges
to the default branch use generic credentials in jobs on that branch. Extra
branches and tags still need bot-inaccessible ref protection or a non-bot
environment reviewer. Tend verifies the exact generated workflows, rejects
other workflows whose environment use is dynamic or hidden behind an external
or ref-qualified reusable workflow, and reserves Tend's operational environment
for the generated jobs. The harness keeps its long-lived credentials out of the
agent process. A bot-controlled workflow cannot reach the bot token or model
auth. A release token or trusted-publishing identity is protected only when
its job stays on a ref the bot cannot update, such as an admin-gated tag. The
bot token and model auth live in the repo's `tend` GitHub Environment, whose
deployment policy admits only the refs `tend check` confirmed for Tend's
hardened runtime, so a workflow pushed to any other branch is refused them
before its first step. The credential check accepts the default branch as an
explicit yolo risk. It checks other credential-holding environments — ones
that store a secret or whose jobs request `id-token: write` — for a non-bot
reviewer or a policy naming verified refs. A trigger the bot both fires and
steers requires a reviewer even when the policy admits yolo's default branch.
It flags any repo-level secret not explicitly
listed in `secrets.allowed`, where the operational names are refused outright.
`tend check --fix` creates the environment and sets its policy; moving the
secrets into it stays manual — their values can't be read back.

**Config pinning** — before the agent starts, both harnesses restore every
`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `AGENTS.override.md`, `.claude/`,
and `.agents/` in the tree, at any depth, from the base branch. Both harnesses
also restore `.mcp.json`, `.claude.json`, `.gitmodules`, `.ripgreprc`, and
`.husky`, blocking startup-time code execution and prompt injection from a PR's
own copy of these files.

**Immutable releases** lock the assets and tag of each release published
after the setting is enabled. The body is not locked — a write-access actor
can still edit an immutable release's notes. `tend check` requires the setting and
`--fix` enables it before the next release. Reading the setting takes
repository admin, so a run as the bot checks the newest release's own
`immutable` flag instead.

**Rate limiting** — Burst detection (10 PRs and 10 issues per 20 minutes,
checked independently) and daily spike detection halt the bot before runaway
loops cause damage.

**Fixed prompts** — Workflow prompts come from the action, not from
attacker-controlled input like PR descriptions or comments.

Full threat model: [docs/security-model.md](docs/security-model.md).

## Configuration

`.config/tend.yaml` — only `bot_name` is required. The default harness runs
Claude; `harness: codex` selects OpenAI Codex (see
[Harnesses](#harnesses) below).

```yaml
bot_name: my-project-bot

# Codex installs pin both values; omit both to use Claude.
# harness: codex
# model: gpt-6-sol
# effort: medium   # low | medium | high | xhigh; Claude also accepts max
# args: [--max-turns, "40"]   # exact additional CLI arguments
```

To pause tend, set the `TEND_ENABLED` repository variable to `false`:

```bash
gh variable set TEND_ENABLED --body false
```

Every tend job that runs the agent is then skipped before it starts, with no
regeneration or commit, and `gh variable delete TEND_ENABLED` resumes them.
Runs already queued or in progress finish; `gh run cancel` stops one. GitHub
evaluates the check before a job enters the `tend` environment, so the
variable must be set on the repository or its organization, not in that
environment. Codex's credential refresher keeps running, so a paused
subscription's tokens stay valid. The bot's write access lets it change the
variable too, so the pause is an operating switch rather than a security
control: to cut off a misbehaving bot, revoke its PAT.

The secrets, stored in the repo's `tend` environment (install-tend creates
it; `tend check` verifies it), depend on the harness:

| Harness    | Required secrets                                                                                                         |
| ---------- | ----------------------------------------------------------------------------------------------------------------------- |
| `claude`   | `TEND_BOT_TOKEN` + one of `CLAUDE_CODE_OAUTH_TOKEN` (subscription) or `ANTHROPIC_API_KEY` (API-billed)                   |
| `codex`    | `TEND_BOT_TOKEN` + either `OPENAI_API_KEY`, or the subscription trio `CODEX_AUTH_JSON`, `CODEX_REFRESH_AUTH_JSON`, and `CODEX_REFRESH_PAT` |

`TEND_BOT_TOKEN` is the bot account's PAT — see
[example config](docs/tend.example.yaml) for scopes.
`CLAUDE_CODE_OAUTH_TOKEN` is from `claude setup-token`. The API keys are
from console.anthropic.com and platform.openai.com. See
[Codex (experimental alternative)](#codex-experimental-alternative) for the
subscription trio.
[docs/security-model.md](docs/security-model.md) has the full leak
breakdown.

All other options — setup commands, protected branches, workflow overrides,
schedules — are documented in
[`docs/tend.example.yaml`](docs/tend.example.yaml).

## Project context

Tend reads `CLAUDE.md` like any Claude Code session — build commands, test
commands, project conventions all go there.

For tend-specific instructions, add a skill overlay at
`.claude/skills/running-tend/SKILL.md`. Common uses: recording which CI
workflow names `tend-ci-fix` watches, PR title conventions, label policies.

## Harnesses

Tend supports Claude and Codex. Pick whichever fits the credentials and
billing path that already work for you; both run the same workflows and
skills.

### Claude (default)

Runs the official `claude` binary headless (`claude -p`) in the shared sandbox
boundary behind a local credential-injecting proxy: the bot token and
the Anthropic credential live only in the proxy, never in the agent's
environment. Two auth modes:

- **`CLAUDE_CODE_OAUTH_TOKEN`** (recommended with a Claude
  subscription) — Claude Code OAuth token from `claude setup-token`,
  funded by the subscription's usage limits.
- **`ANTHROPIC_API_KEY`** — standard API key from console.anthropic.com,
  billed per token against the Console org. Pick this when there's no
  Claude subscription, when the bot should bill against a dedicated
  Console org, or when per-key revocation matters.

The proxy injects whichever you set into requests to api.anthropic.com; the
agent itself only ever holds a dummy.

### Codex (experimental alternative)

Installs `@openai/codex` and invokes `codex exec` in the shared sandbox boundary.
GitHub access goes through Tend's exact-host proxy. Under API auth, the OpenAI
key is read from stdin by OpenAI's narrow Responses API proxy and is never
placed in the agent's environment. Under subscription auth, the sandbox gets
an expiring access-only `auth.json`, never the rotating refresh token. A bundled
`AGENTS.md` teaches Codex to resolve tend's slash commands to skill markdown.

Codex's own nested sandbox is disabled. One transient systemd unit per run is
the single filesystem, network, seccomp, and process-lifetime boundary for both
harnesses.

Two auth modes:

- **ChatGPT Plus or Pro (experimental):** concurrent jobs receive
  `CODEX_AUTH_JSON`, an access-only bundle that Codex cannot refresh. A single
  serialized weekly workflow holds `CODEX_REFRESH_AUTH_JSON`, rotates it, then
  publishes the next access-only bundle using `CODEX_REFRESH_PAT`.
- **API:** `OPENAI_API_KEY` is a standard pay-per-token key from
  platform.openai.com.

The split fixes the old race: no consumer receives the rotating refresh token,
so concurrent jobs cannot invalidate one another's refresh state. This is
experimental because it uses Codex's internal `chatgptAuthTokens` mode. The
weekly job runs Codex's built-in refresh and persists the updated full bundle.
Tend pins and tests the Codex version, but an OpenAI change can still break the
weekly refresh until Tend updates.

## Badge

A badge signals the repo is maintained with tend:

```markdown
[![maintained with tend](https://img.shields.io/badge/maintained_with-tend-bba580?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwxNikgc2NhbGUoMC4wMTI1LC0wLjAxMjUpIiBmaWxsPSIjZmZmIiBzdHJva2U9Im5vbmUiPjxwYXRoIGQ9Ik02ODAgMTEyOCBjNjIgLTk2IDY5IC0xNzggMjAgLTI0MSAtMTcgLTIyIC0yMCAtNDAgLTIwIC0xMzQgbDEgLTEwOCAyMSAyOCBjMTEgMTYgMzAgNDcgNDIgNzAgMTIgMjIgMzIgNDkgNDYgNTkgMzcgMjcgMTE0IDM4IDE4NCAyNyA5MyAtMTUgOTQgLTE4IDQ0IC03OSAtNzIgLTg4IC0xMDkgLTExMyAtMTc2IC0xMTcgLTMxIC0yIC02NCAxIC03MiA2IC0yMyAxNSAyMSA1NiAxMDcgOTggNDAgMjAgNzEgMzggNjkgNDAgLTYgNyAtODggLTE3IC0xMjYgLTM3IC00OSAtMjUgLTEwMCAtNzggLTEyMSAtMTI1IC0xNSAtMzMgLTE5IC02NiAtMTkgLTE4OCAwIC0xNTcgOCAtMTk1IDUwIC0yMzIgMTcgLTE2IDM2IC0yMCA4NSAtMTkgNjIgMSA2MyAxIDczIC0zMiA5IC0zMiA5IC0zMyAtMjIgLTQwIC01MCAtMTIgLTEzMiAtNyAtMTY0IDEwIC00MCAyMSAtNzkgNjkgLTkyIDExNCAtNSAyMCAtMTAgMTAyIC0xMCAxODIgMCA4MCAtNSAxNjIgLTExIDE4NCAtMjIgNzkgLTEzNSAxNjYgLTIzNCAxODEgLTM3IDYgLTM1IDMgMzAgLTI4IDc4IC0zOSAxNDQgLTkxIDEzMiAtMTA0IC01IC00IC0zNyAtOCAtNzEgLTggLTc3IDAgLTExNyAyNCAtMTgyIDEwOSAtNTIgNjggLTUxIDcwIDQyIDg1IDcxIDExIDE0MyAwIDE4MyAtMjkgMTYgLTExIDQwIC00MyA1NCAtNzMgMTMgLTI5IDMyIC01OSA0MSAtNjYgMTQgLTEyIDE2IC03IDE2IDU4IDAgNTkgNCA3NyAyMyAxMDIgMTkgMjYgMjMgNDYgMjUgMTMwIDMgNjcgMCA5OSAtNyA5OSAtNyAwIC0xMSAtMjMgLTEyIC01NyAwIC0zMiAtNiAtNzYgLTEyIC05NyBsLTEyIC00MCAtMjcgMzIgYy0zNCA0MSAtNDMgOTYgLTI0IDE1MSAxNCA0MSA3NSAxNDEgODYgMTQxIDMgMCAyMSAtMjQgNDAgLTUyeiIvPjwvZz48L3N2Zz4K)](https://github.com/max-sixty/tend)
```

The install-tend skill adds this automatically during setup; the kickoff
prompt lets you opt out.

## License

MIT
