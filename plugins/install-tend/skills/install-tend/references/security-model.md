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
- **Operating on a tag.** A ruleset with the `creation` and `update`
  rules covering all tags (`~ALL` on a `tag`-target ruleset), admin-only
  bypass. Blocks the bot from pushing a new tag and from force-pushing
  (re-pointing) an existing one. `update` is required: force-pushing an
  existing tag maps to `update`, not `creation`, so without it a write-
  access actor could re-point an admin-pushed `v1.2.3` to a malicious
  commit. `deletion` is not in the chain: recreation is already blocked
  by `creation`, so a deleted tag can't be substituted with malicious
  code; the only damage is brief availability of the tag itself.

The "all tags" scope is deliberate: matching every tag removes a per-repo
pattern choice and keeps the chain a single uniform rule. Adopters that
need a narrower or layered configuration (per-pattern rulesets,
no-bypass immutability on release tags for repos that publish actions
consumed via tag pins, required-reviewer environment gates for per-deploy
human approval) can layer additional rulesets and environment protection
rules on top; install-tend packages the simplest configuration that holds
the chain.

Deploy and publish workflows declare a GitHub Environment whose
`deployment_branch_policy` lists only those admin-gated refs (the default
branch and/or all tags). Release secrets live in those environments, not
at repo level. A leaked bot token can push a non-default branch, but it
cannot push to the default branch and cannot push any tag, so no
bot-pushed ref matches an admin-gated policy entry. The deploy job is
rejected before it can read the secret. No admin operation → no
admin-gated ref → no environment access → no secret.

That guarantee assumes the privileged workflow is reachable only by
updating an admin-gated ref: trigger on `push: tags:` (release) or
`push: branches: [<default-branch>]` (continuous deploy). Other triggers
(`workflow_dispatch`, `release: published`, `deployment`, `schedule`,
chained dispatches) can be initiated by a write-scoped bot against an
allowed ref, so the env policy alone does not gate them. Workflows
keeping those triggers need trigger-specific containment, typically
required reviewers on the Environment, before release or deploy secrets
are migrated there.

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

## Codex auth.json is not supported

`harness: codex` accepts only `OPENAI_API_KEY`. The subscription
`auth.json` path is not exposed because Codex rotates that refresh
token on every API call and invalidates the prior one after a short
grace window. Tend runs multiple workflows concurrently
(review/mention/triage/nightly/…), so each in-flight job's call
invalidates the credential the other in-flight jobs are using — a
scheduled refresher works around the ~8-day full-rotation schedule
but cannot solve the per-call collision between concurrent jobs.
OpenAI's own
[CI/CD auth guide](https://developers.openai.com/codex/auth/ci-cd-auth)
forbids sharing one `auth.json` across concurrent jobs and
discourages it for public repos.

If `auth.json` was previously installed, replace it with an
`OPENAI_API_KEY` secret and delete the `CODEX_AUTH_JSON` and
`CODEX_REFRESH_PAT` secrets plus any `codex-auth-refresh.yaml`
workflow.

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
| ↳ Claude OAuth token | `harness: claude` or `harness: claude-interactive`: authenticates Claude Code to the Anthropic API. |
| ↳ `OPENAI_API_KEY` | `harness: codex`: standard OpenAI API key, per-token billing. The subscription `auth.json` path is not supported (see above). |

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
