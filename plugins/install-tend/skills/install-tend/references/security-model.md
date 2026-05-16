# Security model (install-time reference)

The security decisions made during install and their rationale. This file
ships with the plugin so it resolves in any repo the skill runs in. The
canonical, full threat model is maintained in the tend source repo at
https://github.com/max-sixty/tend/blob/main/docs/security-model.md; this is
the subset an installing agent needs.

## The chain: every privileged path is admin-gated

Tend runs an agent with write access on attacker-controlled input. The
boundary is structural: every code path into a privileged workflow chains
back to an admin-controlled operation, and the bot has write, not admin.

The two admin-gated operations are:

- **Merging to the default branch.** A ruleset with the `update` rule on
  the default branch, admin-only bypass. Blocks the bot from landing code
  on the default branch.
- **Operating on a release tag.** A ruleset with the `creation`,
  `update`, and `deletion` rules on the release tag pattern, admin-only
  bypass. Blocks the bot from pushing, rewriting, or deleting any
  matching tag. Creating or repairing a release tag is itself an admin
  operation.

Deploy and publish workflows declare a GitHub Environment whose
`deployment_branch_policy` lists only those admin-gated refs (the default
branch and/or the release tag pattern). Release secrets live in those
environments, not at repo level. A leaked bot token can push a branch or a
non-release tag, but neither ref matches an admin-gated policy entry, so
the deploy job is rejected before it can read the secret. No admin
operation → no admin-gated ref → no environment access → no secret.

The composite action refuses to start if the default branch is unprotected.

Everything else (config pinning, rate limiting, fixed prompts) is defense
in depth.

## If a token leaks

| Token | Lifetime | If leaked, attacker can... | ...but cannot |
|-------|----------|----------------------------|---------------|
| Bot token (PAT) | Long-lived | Push to unprotected branches, create PRs, impersonate the bot, indefinitely | Merge PRs (merge restriction), push to the default branch, access release secrets (environment-protected) |
| Bot token (App) | ~1 hour | Same as PAT, until the token expires | Same, plus auto-expiry |
| Claude OAuth | Long-lived | Run Claude sessions billed to the account | Access GitHub |
| `OPENAI_API_KEY` | Until revoked | Run Codex/OpenAI calls billed to the account | Access GitHub |
| `CODEX_AUTH_JSON` | ~8-day refresh window | Run ChatGPT API calls as the minting account. Personal account: read chat history, access custom GPTs, exhaust quota. Dedicated account: burn subscription quota until rotation. | Access GitHub |

## Codex auth.json: mint from a dedicated bot account

`auth.json` is an OAuth refresh token bound to a ChatGPT account; a leak
gives the attacker that account's plan resources. OpenAI's CI/CD auth guide
(https://developers.openai.com/codex/auth/ci-cd-auth) discourages this path
for public or open-source repos, on the assumption the token comes from a
personal account. Tend's mitigation is to mint `auth.json` from a ChatGPT
account dedicated to the bot: required on public repos, recommended on
private. That narrows a leak to subscription-quota burn until rotation,
comparable in scope to an `OPENAI_API_KEY` leak (agent-runtime access only,
no GitHub). Flat-rate subscription billing also usually beats per-token API
billing on a busy repo, so the dedicated-account `auth.json` is the install
default. Revoke a leaked token at
https://chatgpt.com/#settings/Personalization.

## Codex static-secret rotation

Codex rotates the refresh token on use with a ~1-hour grace window for the
old token. In CI the rotated token lands in an ephemeral runner while the
GitHub secret still holds the now-invalid value. After ~8 days Codex's
proactive refresh fires in the next workflow to run, and within ~1 hour
every later run 401s permanently. OpenAI's guide also forbids sharing one
`auth.json` across concurrent jobs. Two safe paths:

- **Manual rotation.** Re-run `CODEX_HOME=/tmp/codex-tend codex login
  --device-auth` every ~6 days and re-set the secret. Acceptable only when
  consumer workflows are rare enough that the day-8 rotation race is
  unlikely.
- **Automated refresher.** A scheduled workflow refreshes the token and
  updates `CODEX_AUTH_JSON` before any consumer workflow can trigger a
  rotation. Its PAT (`CODEX_REFRESH_PAT`, fine-grained, `secrets: read and
  write` on the repo) must not be a plain repo secret: the bot has
  `workflow` scope and can push workflow files on feature branches that
  read repo secrets, which would escalate it from write collaborator to
  rewriting every repo secret. Store the PAT in an Environment (for example
  `codex-auth-refresh`) pinned to `main`; GitHub gates secret injection on
  the workflow ref before the job starts, so bot-pushed feature-branch
  workflows cannot read it. Reference implementation to copy:
  https://github.com/max-sixty/tend/blob/main/.github/workflows/codex-auth-refresh.yaml

## Token assignment

Use a single bot token across all workflows for consistent identity. The
merge restriction caps blast radius regardless of which token is used.

Two tokens are needed: the bot's PAT (or GitHub App) credential, plus a
harness-auth credential whose form depends on `harness` in
`.config/tend.yaml`.

| Token | Purpose |
|-------|---------|
| Bot token (PAT or App) | GitHub API and git operations. Consistent bot identity. |
| Harness auth (one of, per harness) | Authenticates the agent runtime. |
| ↳ Claude OAuth token | `harness: claude`: authenticates Claude Code to the Anthropic API. |
| ↳ `CODEX_AUTH_JSON` | `harness: codex`, subscription-funded: the `auth.json` Codex writes after `codex login --device-auth`. Default recommendation; on public repos mint it from a dedicated ChatGPT account (see above). |
| ↳ `OPENAI_API_KEY` | `harness: codex`, API-billed: standard OpenAI API key, per-token billing. Alternative when minting a dedicated ChatGPT account isn't desired. |

A single bot token is safe across workflows because the merge restriction
caps the blast radius. One token also gives consistent bot identity for
reviews and comments and avoids the `github-actions[bot]` branding.

## How tokens flow through workflows

Two independent authentication paths exist in every workflow:

1. **Git CLI** (`git push`): authenticates with the token from
   `actions/checkout`. When no explicit token is passed it defaults to
   `GITHUB_TOKEN` scoped by the `permissions:` block; passing an explicit
   token swaps in that token's scopes.
2. **GitHub API** (`gh pr create`, `gh api`): `claude-code-action`
   overwrites the `GITHUB_TOKEN` env var with its `github_token` input.

All workflows should pass the bot token to both paths.

Bind the bot token to `GITHUB_TOKEN`, not `GH_TOKEN`. `GITHUB_TOKEN` is
auto-injected by GitHub Actions and read by most third-party tools;
overriding it gives one bot identity everywhere in the job. `GH_TOKEN`
only overrides the `gh` CLI; anything else still sees the auto-injected
`github-actions[bot]` token.
