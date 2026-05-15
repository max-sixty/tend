# Tend follow-ups

Deferred work and unimplemented options. Each entry should justify the cost
of building it if revisited.

## Cut tend over to harness = "codex" (post-release)

The Codex harness landed but tend itself still runs on Claude. The cutover
needs the release sequence:

1. Land the harness support PR on `main`.
2. Cut release `0.0.19` — bumping the `v1` tag to include `codex/action.yaml`.
3. Edit `.config/tend.yaml`: add `harness: codex` (and optionally
   `effort: medium`). Set `model: gpt-5.5` explicitly or let the
   default win.
4. Set `CODEX_AUTH_JSON` secret on `max-sixty/tend` (or `OPENAI_API_KEY`).
   Drop `CLAUDE_CODE_OAUTH_TOKEN` from `secrets.allowed` once unused.
5. `uvx tend@latest init` to regenerate workflows. Commit both the config
   and the regenerated `tend-*.yaml` files in one commit.
6. The first nightly run after merge dogfoods the new path; watch
   `/activity` for the first review/triage and confirm token-usage parsing
   reports non-zero values.

Doing this in the same PR that ships the action would temporarily break
tend's own CI between merge and the release tag bump.

## Auth: GitHub App alternatives to PAT

Both alternatives replace the classic PAT (long-lived, leak-permanent) with
a GitHub App installation token (~1 h lifetime, repo-scoped). This is the
single highest-impact change for token-leak risk.

### Model A: token-minting service

Adopter installs our GitHub App; `tend init` generates the same workflow
files. The only auth change is an OIDC call to our service that mints a
scoped installation token per workflow run. Workflows still live and run
in the adopter's repo.

```yaml
- uses: max-sixty/tend/auth@v1   # OIDC → our service → scoped token
  id: auth
- uses: actions/checkout@v6
  with:
    token: ${{ steps.auth.outputs.token }}
- uses: max-sixty/tend@v1
  with:
    github_token: ${{ steps.auth.outputs.token }}
    claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

Trust model: standard GitHub App — adopters trust the App by installing it,
like installing Codecov or Renovate. We hold the App private key; adopters
hold their own Claude OAuth token. A workflow-run OIDC token
(`id-token: write`) proves the caller's repo identity to our service.

Could be extended to push workflow updates via PR, but that requires a
webhook handler to detect config changes.

### Model B: full webhook handler

Adopter installs our GitHub App, adds `.config/tend.yaml`, done — no
workflow files. GitHub sends raw events to our service; we run the logic
(engagement verification, concurrency, dispatch) and execute Claude on our
infrastructure (or dispatch back to the adopter's runners).

Most cohesive UX, and partially addresses the fork-PR gap — we receive
inline review-comment webhooks regardless of fork status.

Trade-offs: a compromise of our infra exposes write access to every
adopter's repo *and* their code. Anthropic token has three options:

- Adopter hands it to us; we hold it. If our service is compromised, the
  attacker gets every adopter's Claude token.
- We provide Claude access and bill the adopter. Simpler for them; we take
  on billing and usage management.
- `workflow_dispatch` back to their runners. Token stays in their secrets;
  adds latency and complexity.

## Auth: triage + fork privilege model

Currently only `write + branch protection` exists. The planned `mode` field
in `.config/tend.yaml` would select between two models:

| | **Triage + fork** | **Write + branch protection** (current) |
|---|---|---|
| Bot collaborator level | Triage | Write |
| Bot pushes code to | Own fork | Target repo branches |
| Creates PRs | From fork | Same-repo |
| Approvals count for required reviews | No | Yes |
| Branch protection required | **No** | **Yes** — primary security boundary |
| Leaked PAT blast radius | Comments/reviews; fork write only | Full write to target repo |
| Setup complexity | Low | Medium |

`Triage + fork` would be the recommended default. The bot pushes to its
own fork and creates cross-fork PRs:

```bash
git remote add fork https://x-access-token:${BOT_TOKEN}@github.com/${BOT_NAME}/${REPO}.git
git push fork fix/ci-123
gh pr create --repo ${TARGET_REPO} --head ${BOT_NAME}:fix/ci-123
```

Limitations: triage-level approvals don't satisfy required-review policies,
and triage can't push to human PR branches — the bot posts review
suggestions instead.

## Security hardening — deferred

From the old `docs/security-model.md` "what we could do but don't" — none
implemented yet:

- **Haiku pre-screening of diffs.** Cheap fast-model pass scanning for
  suspicious patterns (build-script modifications, `curl | sh`,
  base64-encoded strings, env-var reads targeting secret names). ~$0.001
  per PR. Not a security boundary (trivial to evade) but useful as a
  tripwire against unsophisticated attacks.
- **Read-only mode for fork PRs.** Restrict `allowed_tools` to
  `Glob`/`Grep`/`Read` + comment-posting MCP tools — no
  `Bash`/`Edit`/`Write`. Closes the attacker-controlled-code-execution
  gap entirely for fork PRs; trade-off is no suggested fixes on fork PRs,
  only reviews.
- **Network isolation.** Self-hosted runners with outbound traffic
  restricted to GitHub and Anthropic API endpoints. Not viable on
  GitHub-hosted runners; significant infra overhead self-hosted.
- **Subprocess environment scrubbing.** `claude-code-action` supports
  `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`. Currently activated only when
  `allowed_non_write_users` is set; could enable for all fork PRs. Naive
  `echo $GITHUB_TOKEN` attacks would fail, though a subprocess can still
  read the parent's unscrubbed env via `/proc/$PPID/environ`
  (same-user, no privilege barrier on GitHub-hosted runners).
- **Workflow dispatch isolation.** Split each workflow into an analysis
  job (`GITHUB_TOKEN` only, reads the diff, produces a plan) and a push
  job (bot token, separate workflow triggered by `workflow_run`). The bot
  token never enters a job that touches attacker-controlled code.
  Significant complexity — every workflow becomes two with artifact
  passing between them.

## Worker: Phase 2 LLM summary of `/activity`

A consumer (scheduled job or the Worker calling Claude) reads `/activity`
and writes a short prose summary of what tend's been up to; the summary
lives in KV and is what the site renders. If the summary wants a longer
span than the last week (beyond GitHub's ~90-day events window or one
Search page), a KV/D1 accumulator that appends activity as it arrives
earns its keep — until then, demand-fetch is cheap enough.
