# Security model (install-time reference)

The security decisions made during install and their rationale. This file
ships with the plugin so it resolves in any repo the skill runs in. The
canonical, full threat model is maintained in the tend source repo at
https://github.com/max-sixty/tend/blob/main/docs/security-model.md; this is
the subset an installing agent needs.

## Two boundaries

Tend runs an agent with write access on attacker-controlled input. There are
two load-bearing boundaries, one per path code can take to run with
privileges:

- **Merge restriction.** A ruleset (or branch protection) stops the bot
  merging to protected branches regardless of review. Covers code that
  reaches the default branch through a merge. The composite action refuses
  to start if the default branch is unprotected.
- **Environment-protected secrets.** Release and deploy secrets in a GitHub
  Environment with required reviewers. Covers code that runs without a
  merge, which the merge restriction does not touch: a tag push, a release,
  a `workflow_dispatch`/`workflow_run`/`repository_dispatch` job, a
  `deployment` API call, a `pull_request_target` workflow, or a `schedule`
  job. The deploy job cannot read the secret until a reviewer approves; the
  actor who triggered the deploy must not be able to approve it ("Prevent
  self-review", off by default), and the bot must be neither a reviewer nor
  an admin.

Everything else (config pinning, rate limiting, fixed prompts) is defense in
depth.

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
