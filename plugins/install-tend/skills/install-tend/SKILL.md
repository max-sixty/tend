---
name: install-tend
description: Sets up tend — an autonomous junior maintainer for a GitHub repo, powered by Claude or OpenAI Codex — that reviews PRs, triages issues, and fixes CI. Creates config, generates workflows, configures secrets and branch protection via API, creates the bot account, and provisions the harness auth token (Claude OAuth or OpenAI Codex auth.json/API key). Use when setting up tend on a new repo or when asked to install/configure tend.
---

# Install Tend

Set up tend on the current repo. If the user hasn't supplied a bot name,
get one via `AskUserQuestion` before step 1 using the candidate-generation
pattern from step 6 (`<repo>-bot`, `<repo>-tend`, `tend-<repo>`, parallel
availability check, present available ones). The user can pick "Other"
to supply a custom name.

When asking the user questions during these steps, use the `AskUserQuestion`
tool — present concrete options when there are clear choices (e.g. bio
stance, badge style, secret-migration confirmation).

When a question requires the user to do something off-screen (visit a URL,
run a command, paste a value back), spell the next step out in the question
or option description: the exact web link, the exact command. "Generate a
token on the registry's site" is not enough — give the URL. The user should
not have to ask "where do I do that?".

## Kickoff

Before running step 1, choose the harness and lay out the plan:

- Ask via `AskUserQuestion` which harness to use:
  - **Claude (Anthropic)** — uses a Claude Code OAuth token (recommended
    for adopters with an eligible Claude subscription) or a
    console.anthropic.com API key. Starting 2026-06-15, eligible-plan
    `claude-code-action` runs draw from a separate monthly Agent SDK
    credit (one-time opt-in then auto-refresh); using OAuth puts that
    bundled allowance to work. Enable "extra usage" in the Console so
    credit exhaustion overflows to API rates instead of hard-stopping
    CI. API key is the alternative when the user is on an ineligible
    plan (e.g. seat-based Enterprise Standard), has no subscription to
    draw on, or wants a dedicated billing surface and per-key
    revocation.
  - **Codex (OpenAI)** — uses a ChatGPT Plus/Pro/Business `auth.json`
    (subscription, recommended) or an OpenAI API key (pay-per-token).
    Public repos require `auth.json` from a ChatGPT account dedicated
    to the bot. Detail in ${CLAUDE_SKILL_DIR}/references/security-model.md.
- List the steps you'll be running (the section headings below: Create
  config → Generate workflows → Branch protection → Skill overlay →
  Badge → Bot account → Harness auth → Bot token → Grant access →
  Bot bio → Commit) so the user knows what's coming.
- Tell them it typically takes 5–10 minutes of their hands-on time
  (browser logins, OAuth approvals, occasional copy-paste); the agent
  drives the rest.
- Confirm via `AskUserQuestion` ("Ready to start?") before beginning
  step 1. Don't proceed until they say yes.

Follow each step in order. Skip steps that are already done — check each
prerequisite before acting. Derive `REPO` once at the start:

```bash
gh auth status
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
echo "$REPO"
```

Confirm with the user that `$REPO` is the canonical repo where tend
will run — not a fork. Every command below passes `--repo "$REPO"`
explicitly, so if the working directory is a fork clone, just
override the variable with the canonical `owner/name` and continue;
no need to touch git remotes.

## Browser sessions

Steps 6 and 8 need a browser session logged in as the bot.
`mcp__claude-in-chrome__*` automation can drive both when available;
otherwise, give the user URLs and wait for confirmation. Before acting
as the bot, verify the logged-in user via the avatar menu.

## 1. Create config

Create `.config/tend.yaml` with at minimum `bot_name`, plus `harness` if
the user chose Codex (Claude is the default and can be omitted). See
README.md for all available config sections (`secrets:`, `setup:`,
`workflows:`).

```yaml
bot_name: <bot-name>
# For Codex, also:
# harness: codex
# effort: medium   # optional: minimal | low | medium | high
```

Check whether the repo already has a bot token secret under a non-default name:

```bash
gh secret list --repo "$REPO" --json name --jq '.[].name'
```

If a bot-token-like secret exists (e.g., `GH_BOT_TOKEN`, `ROBOT_PAT`),
suggest overriding the default name rather than creating a duplicate:

```yaml
secrets:
  bot_token: GH_BOT_TOKEN
```

Any repo-level secret not in `secrets.allowed` triggers a `tend check`
warning. Classify each non-bot secret and act now — don't defer:

- Build/observability tokens (e.g., `CODECOV_TOKEN`, `SENTRY_DSN`) are
  fine at the repo level. Add them to the allowlist:

  ```yaml
  secrets:
    allowed: ["CODECOV_TOKEN"]
  ```

- Release secrets (registry tokens like `PYPI_TOKEN`/`NPM_TOKEN`, signing
  keys, deploy credentials) at the repo level are reachable from any
  workflow run, including ones a write-access bot can trigger with no
  merge. Don't allowlist them. Migrate each to a GitHub Environment whose
  deployment policy pins to the admin-gated refs from §3 (the default
  branch, and the release tag pattern for tag-triggered deploys). The bot
  can reach neither ref, so it cannot reach the secret.

  Migrate the secret: recreate it on the Environment, delete the
  repo-level copy (confirm via `AskUserQuestion` first), and set
  `environment: <name>` on the publishing job.

  Configure the deployment policy. Allow the default branch:

  ```bash
  REPO=<owner>/<repo>; ENV=<name>
  DEFAULT_BRANCH=$(gh api "repos/$REPO" --jq .default_branch)
  gh api --method PUT "/repos/$REPO/environments/$ENV" \
    -F 'deployment_branch_policy[protected_branches]=false' \
    -F 'deployment_branch_policy[custom_branch_policies]=true'
  gh api --method POST "/repos/$REPO/environments/$ENV/deployment-branch-policies" \
    -f "name=$DEFAULT_BRANCH" -f type=branch
  ```

  For a tag-triggered deploy (workflow with `on: push: tags:`), also allow
  the release tag pattern from §3:

  ```bash
  gh api --method POST "/repos/$REPO/environments/$ENV/deployment-branch-policies" \
    -f "name=$TAG_PATTERN" -f type=tag
  ```

  Verify:

  ```bash
  gh api "/repos/$REPO/environments/$ENV/deployment-branch-policies" \
    --jq '.branch_policies | map({name, type})'
  ```

  Each entry must match a ref class from §3 (default branch and/or release
  tag pattern). Confirm before checking the box.

  Then sweep deploy/publish workflows for triggers that bypass the merge
  restriction; each must declare an Environment so the policy applies. The
  grep misses reusable workflows in other repos and over-matches
  `pull_request_target` references in expressions and step inputs, so read
  each hit:

  ```bash
  grep -RniE 'tags:|workflow_dispatch|release:|schedule:|workflow_run|repository_dispatch|deployment:|pull_request_target' .github/workflows
  ```

  An OIDC-to-cloud deploy has no secret to migrate; the Environment with
  its admin-gated deployment policy plus the cloud provider's trust policy
  is then the only control on that path.

  The original repo-level secret value isn't readable (GitHub secrets are
  write-only), so a fresh token is needed. Ask the user via `AskUserQuestion`
  how to obtain it; recommend whichever fits the registry:

  - **CLI** — if the registry has a token-issuing CLI (e.g., `npm token create`),
    run it and capture the token.
  - **Chrome** — drive the registry's token page via `mcp__claude-in-chrome`
    (most registries — PyPI, crates.io, Docker Hub — only issue tokens via
    the web UI). Some registries (PyPI in particular) force a 2FA reauth
    at token-creation time; Chrome MCP can't drive that second factor.
    If the reauth prompt appears, fall back to Manual.
  - **Manual** — user generates the token themselves on the registry's
    site and pastes it back.

  Whichever route is chosen, include the exact token-creation URL in
  the question or option description (and in the follow-up message if
  manual). Common registries:

  - PyPI: `https://pypi.org/manage/account/token/`
  - npm: `https://www.npmjs.com/settings/<user>/tokens/new` (or `npm token create`)
  - crates.io: `https://crates.io/settings/tokens`
  - Docker Hub: `https://app.docker.com/settings/personal-access-tokens`
  - GitHub Packages / deploy: `https://github.com/settings/tokens`

  For other registries, look up the token page before asking. Accept any
  other route the user suggests. Never ask the user to dig the old token
  out of their password manager and re-paste it — issuing a fresh token
  and revoking the old one is part of the migration's point.

Discover existing CI workflows so tend-ci-fix can watch them:

```bash
grep -l 'push:\|pull_request' .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null
```

For each match, extract the workflow `name:` field. These are the workflows
that run tests, linting, or builds — tend-ci-fix should watch them. Configure:

```yaml
workflows:
  ci-fix:
    watched_workflows: ["ci", "lint"]  # names of workflows to watch
```

If no CI workflows exist, either skip ci-fix (`enabled: false`) or help the
user create one first.

Ask via `AskUserQuestion` (`multiSelect: true`) which other overrides
they want to set. Skip-all is fine — defaults are sensible:

- Setup steps (system deps, language version, pre-build hooks)
- Workflow conditions (e.g., skip review on `tend:dismissed` PRs — see below)
- Schedule overrides (cron timing for nightly/weekly)
- Permissions / timeouts on specific jobs
- Top-level env vars

For each selected category, follow up with a free-text ask, then write
the override into `.config/tend.yaml`. See the next subsection for
override syntax.

### Customizing generated workflow YAML

The generator owns every `tend-*.yaml` file — direct edits are lost on the next
`uvx tend@latest init`. Instead, set `workflow_extra` (top-level) or
`jobs.<name>` (job-level) overrides in `.config/tend.yaml`. Overrides follow
RFC 7396 (JSON Merge Patch): mappings deep-merge, scalars and lists replace.

Common example — skip review on PRs labeled `tend:dismissed` (so authors can
opt out of re-reviews after the initial pass). Because scalars replace under
Merge Patch, the override must duplicate the default draft check:

```yaml
workflows:
  review:
    jobs:
      review:
        if: "github.event.pull_request.draft == false && !contains(github.event.pull_request.labels.*.name, 'tend:dismissed')"
```

See ${CLAUDE_SKILL_DIR}/references/tend.example.yaml for more override
examples (extending permissions, timeouts, top-level env vars).

## 2. Generate workflows

```bash
uvx tend@latest init --with-install-test
```

`--with-install-test` adds a one-shot `tend-install-test.yaml` workflow
that runs on the install PR to verify secrets are set and the committed
workflows match the generator's current output. The next nightly regen
runs `uvx tend@latest init` without the flag, and the init cleanup step
removes the file from the default branch.

Verify workflow files appear in `.github/workflows/tend-*.yaml`. Run
`uvx tend@latest check` to validate branch protection, secrets, and bot access.

Check for workflows using `anthropics/claude-code-action`:

```bash
grep -rl 'anthropics/claude-code-action' .github/workflows/ 2>/dev/null
```

If found, delete them — tend replaces claude-code-action entirely. Remind the
user that team members should @-mention the bot account instead of `@claude`.

## 3. Ref protection

Two refs can land code that reaches a deploy or publish workflow: the
default branch (via merge) and release tags (via tag push). Restrict both
to admin-only operations so every privileged code path chains back to an
admin action. The bot has write, not admin, so it satisfies neither
bypass.

Survey existing rulesets; skip any slot already covered:

```bash
gh api "repos/$REPO/rulesets" --jq '.[] | {name, target, enforcement}'
```

**Merge restriction on the default branch.** Create if missing:

```bash
gh api "repos/$REPO/rulesets" --method POST --input - << 'EOF'
{
  "name": "Merge access",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] }
  },
  "rules": [{ "type": "update" }],
  "bypass_actors": [{
    "actor_id": 5,
    "actor_type": "RepositoryRole",
    "bypass_mode": "exempt"
  }]
}
EOF
```

**Release tags.** Ask the user via `AskUserQuestion` what pattern their
release tags use. Recommend `[0-9]*` (bare semver, e.g. `0.0.1`) and
`v[0-9]*` (v-prefixed, e.g. `v1.2.3`) as the common options. Store the
choice as `$TAG_PATTERN`; the release-secrets step (§1) references it.

Create one ruleset covering creation, update, and deletion. The bot
(write) is blocked from all three; admins can do all three, so creating
or repairing a release tag is itself an admin operation:

```bash
gh api "repos/$REPO/rulesets" --method POST --input - << EOF
{
  "name": "Release tag operations",
  "target": "tag",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["refs/tags/$TAG_PATTERN"], "exclude": [] }
  },
  "rules": [
    { "type": "creation" },
    { "type": "update" },
    { "type": "deletion" }
  ],
  "bypass_actors": [{
    "actor_id": 5,
    "actor_type": "RepositoryRole",
    "bypass_mode": "exempt"
  }]
}
EOF
```

## 4. Create skill overlay (recommended)

Create `.claude/skills/running-tend/SKILL.md` with tend-specific project
guidance. This skill is loaded by tend workflows alongside the generic
`tend-*` skills.

**Do NOT duplicate CLAUDE.md** and **do NOT invent project conventions.**

Ask via `AskUserQuestion` (`multiSelect: true`) which tend-specific
preferences they want to capture. Skipping all is fine — the placeholder
below covers that case.

- PR title format (e.g., conventional commits, Jira ticket prefix)
- Labels the bot should apply to its PRs
- Review request routing (specific teams or people)
- Target branch if not the default branch
- Optional nightly actions (e.g., changelog maintenance — specify file and branch)

For each selected item, follow up with a free-text ask to capture the
specifics, then write them into the overlay. If nothing is selected,
create a placeholder:

```markdown
No project-specific tend preferences yet. Add guidance here as
needed — this file is loaded by tend workflows alongside CLAUDE.md.
```

Build commands, test commands, code style, and project structure belong
in CLAUDE.md — tend reads it like any other Claude session.

## 5. Offer to add a badge

If the repo has a README (any of `README.md`, `README.rst`, `README`), offer
to add a "maintained with tend" badge.

Base URL (always include the logo):

```
https://img.shields.io/badge/maintained_with-tend-bba580?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwxNikgc2NhbGUoMC4wMTI1LC0wLjAxMjUpIiBmaWxsPSIjZmZmIiBzdHJva2U9Im5vbmUiPjxwYXRoIGQ9Ik02ODAgMTEyOCBjNjIgLTk2IDY5IC0xNzggMjAgLTI0MSAtMTcgLTIyIC0yMCAtNDAgLTIwIC0xMzQgbDEgLTEwOCAyMSAyOCBjMTEgMTYgMzAgNDcgNDIgNzAgMTIgMjIgMzIgNDkgNDYgNTkgMzcgMjcgMTE0IDM4IDE4NCAyNyA5MyAtMTUgOTQgLTE4IDQ0IC03OSAtNzIgLTg4IC0xMDkgLTExMyAtMTc2IC0xMTcgLTMxIC0yIC02NCAxIC03MiA2IC0yMyAxNSAyMSA1NiAxMDcgOTggNDAgMjAgNzEgMzggNjkgNDAgLTYgNyAtODggLTE3IC0xMjYgLTM3IC00OSAtMjUgLTEwMCAtNzggLTEyMSAtMTI1IC0xNSAtMzMgLTE5IC02NiAtMTkgLTE4OCAwIC0xNTcgOCAtMTk1IDUwIC0yMzIgMTcgLTE2IDM2IC0yMCA4NSAtMTkgNjIgMSA2MyAxIDczIC0zMiA5IC0zMiA5IC0zMyAtMjIgLTQwIC01MCAtMTIgLTEzMiAtNyAtMTY0IDEwIC00MCAyMSAtNzkgNjkgLTkyIDExNCAtNSAyMCAtMTAgMTAyIC0xMCAxODIgMCA4MCAtNSAxNjIgLTExIDE4NCAtMjIgNzkgLTEzNSAxNjYgLTIzNCAxODEgLTM3IDYgLTM1IDMgMzAgLTI4IDc4IC0zOSAxNDQgLTkxIDEzMiAtMTA0IC01IC00IC0zNyAtOCAtNzEgLTggLTc3IDAgLTExNyAyNCAtMTgyIDEwOSAtNTIgNjggLTUxIDcwIDQyIDg1IDcxIDExIDE0MyAwIDE4MyAtMjkgMTYgLTExIDQwIC00MyA1NCAtNzMgMTMgLTI5IDMyIC01OSA0MSAtNjYgMTQgLTEyIDE2IC03IDE2IDU4IDAgNTkgNCA3NyAyMyAxMDIgMTkgMjYgMjMgNDYgMjUgMTMwIDMgNjcgMCA5OSAtNyA5OSAtNyAwIC0xMSAtMjMgLTEyIC01NyAwIC0zMiAtNiAtNzYgLTEyIC05NyBsLTEyIC00MCAtMjcgMzIgYy0zNCA0MSAtNDMgOTYgLTI0IDE1MSAxNCA0MSA3NSAxNDEgODYgMTQxIDMgMCAyMSAtMjQgNDAgLTUyeiIvPjwvZz48L3N2Zz4K
```

Match the `style` parameter used by existing badges in the README. For
example, if the repo uses `style=for-the-badge`, append
`&style=for-the-badge` to the URL. If no existing badges or no style
parameter, use the default (no style parameter needed).

Use `AskUserQuestion` to confirm. Describe the badge briefly in the
question ("an olive-green 'maintained with tend' badge with the tend
wordmark") — do NOT paste the raw `img.shields.io` URL or its base64
logo blob into the chat; the blob is hundreds of characters of noise.
The user only needs to decide yes/no, not eyeball the URL. Insert the
markdown directly into the README on confirmation.

Place it near the top of the README — after the title/heading but
before the first paragraph. If there are already badges on that line,
append to the same line.

If no README exists, skip this step.

## 6. Bot account

```bash
gh api users/<bot-name> --jq '.login,.id' 2>/dev/null && echo "EXISTS" || echo "NOT FOUND"
```

If the account doesn't exist:

1. If the user hasn't chosen a name yet, generate three candidates
   (e.g. `<repo>-bot`, `<repo>-tend`, `tend-<repo>`), check availability
   in parallel, and present the available ones via `AskUserQuestion`:

   ```bash
   for name in cand1 cand2 cand3; do
     gh api "users/$name" >/dev/null 2>&1 && echo "$name: TAKEN" || echo "$name: available"
   done
   ```
2. Navigate Chrome to `https://github.com/signup`.
3. If a verification code is needed and an email-reading skill or MCP is
   available, use it to fetch the latest GitHub verification email
   (`from:github subject:code`); otherwise have the user paste the code.
4. After confirmation, re-verify via API.

## 7. Harness auth token

Branch on the harness chosen in Kickoff.

### 7a. Harness = claude

The Claude action accepts two auth modes; pick whichever the user has.
The action prefers `CLAUDE_CODE_OAUTH_TOKEN` when both are set.

```bash
gh secret list --repo "$REPO" --json name --jq '.[].name' \
  | grep -E -q '^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY)$' \
  && echo "SET" || echo "NOT SET"
```

If not set, ask via `AskUserQuestion` which auth mode to use:

- **OAuth token (recommended for eligible Claude subscribers)** —
  `sk-ant-oat01-…` from `claude setup-token`. Funded by eligible
  subscriptions; from 2026-06-15 these runs draw from a separate monthly
  Agent SDK credit. Have the user opt in to the credit through
  their Claude account once (Anthropic emails instructions; it
  auto-refreshes each cycle after that), and enable "extra usage" in the
  Console if they want credit exhaustion to overflow to API rates
  instead of stopping CI. Token is advertised as 1-year.
- **API key** — `sk-ant-…` from
  `https://console.anthropic.com/settings/keys`. Billed per token against
  the Console org. Pick this when there's no Claude subscription, when
  the bot should bill against a dedicated Console org, or when per-key
  revocation matters. Works for any repo.

For **OAuth token**: before offering the CLI option, check:

- `command -v claude` — if missing, only offer Manual (point them at
  `https://claude.com/claude-code` to install).
- `uname` — the bundled wrapper depends on `bash` + `script(1)` and
  has only been validated on macOS and Linux. On anything else
  (`MINGW*`, `CYGWIN*`, `MSYS*`, `Windows_NT`, etc.), only offer Manual.

- **CLI (recommended on macOS/Linux when `claude` is on PATH)** — run
  the bundled wrapper, which invokes `claude setup-token` (OAuth 2.0
  PKCE, opens browser):

  ```bash
  TOKEN=$("${CLAUDE_SKILL_DIR}/scripts/oauth-token.sh")
  ```

- **Manual** — have the user run `claude setup-token` in their own
  terminal (any machine with Claude Code installed) and paste the
  `sk-ant-oat01-…` token back. Use this on Windows or when the
  wrapper errors out.

Then store the secret:

```bash
echo "$TOKEN" | gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo "$REPO"
```

For **API key**:

Have the user paste the `sk-ant-…` key, then store it:

```bash
gh secret set ANTHROPIC_API_KEY --repo "$REPO" --body "$KEY"
```

### 7b. Harness = codex

Codex supports two auth modes. The `tend/codex` action prefers
`auth.json` when both are set.

Ask via `AskUserQuestion`:

- **ChatGPT subscription (auth.json, recommended)** — billed at the
  Plus/Pro/Business subscription's flat rate. The token carries
  read+write access to the ChatGPT account that minted it, so
  **mint `auth.json` from a dedicated bot account** — required on
  public repos, recommended on private. See
  ${CLAUDE_SKILL_DIR}/references/security-model.md for the leak breakdown.
- **OpenAI API key** — billed per token. Works for any repo. Pick
  this if the user doesn't want to mint a separate ChatGPT account.
  Key from `https://platform.openai.com/api-keys`.

For **auth.json** (recommended):

Ask via `AskUserQuestion` which account the user will sign in as:

- **Dedicated bot account (recommended)** — no personal chat data
  behind the token.
- **Personal account** — token grants access to the user's full
  ChatGPT account if leaked.

Refuse the personal option on public repos; if the user won't mint a
dedicated account, skip to **API key** below. On private repos
accept either, and honor the answer in step 1.

```bash
gh secret list --repo "$REPO" --json name --jq '.[].name' | grep -q CODEX_AUTH_JSON && echo "SET" || echo "NOT SET"
```

If not set:

1. Install codex if missing (`npm i -g @openai/codex`) and create
   the mint dir: `mkdir -p /tmp/codex-tend`.

2. Have the user run, in their own terminal:

   ```bash
   CODEX_HOME=/tmp/codex-tend codex login --device-auth
   ```

   (Don't drive this from Claude's `Bash` tool — codex blocks until
   sign-in and only flushes stdout on exit, so Claude wouldn't be
   able to surface the URL+code in time.) `--device-auth` prints a
   URL and a one-time code; the user opens the URL in any browser
   and signs in as the dedicated bot ChatGPT account chosen above
   (device-code is how they sign in as the bot without juggling
   browser sessions). The dedicated `CODEX_HOME` isolates the bot's
   `auth.json` from the user's personal `~/.codex/` — both coexist,
   no need to log out of personal Codex. Codex writes
   `/tmp/codex-tend/auth.json` once they sign in.

3. After the user confirms they've signed in, read the file and
   set the secret:

   ```bash
   gh secret set CODEX_AUTH_JSON --repo "$REPO" < /tmp/codex-tend/auth.json
   rm -rf /tmp/codex-tend
   ```

4. Set up rotation. The static secret breaks ~8 days after mint —
   the first consumer workflow to run after that triggers a Codex
   refresh, rotates the tokens in an ephemeral runner, and the
   GitHub secret holds the invalidated value. Two paths:

   - **Manual** (low-volume bots): re-run the device-code mint every
     ~6 days and re-set the secret.
   - **Automated refresher** (recommended for concurrent CI): a
     scheduled workflow updates the secret before any consumer can
     trigger a rotation. See
     ${CLAUDE_SKILL_DIR}/references/security-model.md for the threat
     model and the reference workflow to copy in.

     The refresher needs a fine-grained PAT with `secrets: read and
     write`. The bot has `workflow` scope and can push workflow
     files to feature branches that read repo secrets, so a plain
     repo secret would hand the bot a "rewrite any secret"
     credential. Store the PAT in an **Environment** pinned to
     `main` — GitHub rejects branch refs that fail the policy
     *before* injecting secrets, so a bot-pushed feature-branch run
     can't read it.

     Create the environment and pin it to `main`:

     ```bash
     gh api -X PUT "repos/$REPO/environments/codex-auth-refresh" \
       -F 'deployment_branch_policy[protected_branches]=false' \
       -F 'deployment_branch_policy[custom_branch_policies]=true' \
       > /dev/null
     gh api -X POST \
       "repos/$REPO/environments/codex-auth-refresh/deployment-branch-policies" \
       -F 'name=main' -F 'type=branch' > /dev/null
     ```

     Have the user mint a fine-grained PAT on the repo owner's
     account (the bot doesn't have admin) via a pre-filled URL —
     substitute `$OWNER` with the repo owner:

     ```
     https://github.com/settings/personal-access-tokens/new?name=tend-codex-refresh&description=Refresher%20for%20CODEX_AUTH_JSON&target_name=$OWNER&expires_in=none&secrets=write
     ```

     The URL pre-fills name, description, owner, no expiry, and
     `secrets: read+write`. One manual step remains: under
     **Repository access** pick "Only select repositories" →
     this repo. No expiry because the env's `main`-only policy is
     the actual security boundary; calendar rotation adds little.

     Have the user paste the PAT back, then store it in the
     environment:

     ```bash
     gh secret set CODEX_REFRESH_PAT --env codex-auth-refresh \
       --repo "$REPO" --body "$PAT"
     ```

     The reference workflow already includes
     `environment: codex-auth-refresh`.

For **API key**:

```bash
gh secret list --repo "$REPO" --json name --jq '.[].name' | grep -q OPENAI_API_KEY && echo "SET" || echo "NOT SET"
```

If not set, have the user paste the `sk-…` key. Store it:

```bash
gh secret set OPENAI_API_KEY --repo "$REPO" --body "$KEY"
```

## 8. Bot token and secret

The bot's token needs scopes `repo`, `workflow`, `notifications`,
`write:discussion`, `gist`, and `user` (per-scope justifications in
${CLAUDE_SKILL_DIR}/references/tend.example.yaml).

Have the user run, in a bash terminal (Git Bash on Windows works):

```bash
env -u GH_TOKEN -u GITHUB_TOKEN gh auth login --hostname github.com --git-protocol https --web \
  --scopes repo,workflow,notifications,write:discussion,gist,user
```

Unsetting both `GH_TOKEN` and `GITHUB_TOKEN` is required: `gh` checks
them in that precedence, and either being set makes `gh auth login`
short-circuit with "The value of the … environment variable is being
used for authentication" and skip the keyring/device-code flow.
Unsetting them for this one command keeps the user's normal env intact.
(On PowerShell or cmd, `env -u` isn't available — translate to the
shell's unset-then-run equivalent, e.g. PowerShell
`Remove-Item Env:GH_TOKEN, Env:GITHUB_TOKEN; gh auth login …`.)

`gh` prints a one-time code and the URL `https://github.com/login/device`.
The user opens that URL in any browser logged in as the bot, pastes the
code, and authorizes. gh stores the token in keyring and makes the bot
the active account.

`gh auth login` has no `--user` flag — the GitHub user it binds to is
whoever was logged into github.com in the approving browser session.
Verify before continuing:

```bash
gh api user --jq '.login'
```

This must print the bot name. Anything else means the wrong account
approved the device code — run `gh auth logout --user <wrong-name>`
and retry. Don't proceed to the secret-set step until this matches.

Switch gh back to the maintainer (whose token has admin on the repo),
copy the bot's token to the repo secret, and verify:

```bash
gh auth switch --user <maintainer>
gh auth token --user <bot-name> | gh secret set TEND_BOT_TOKEN --repo "$REPO"
gh secret list --repo "$REPO"
```

## 9. Grant bot access

All invitation acceptance in this step uses the bot's token from step 8 via
`GH_TOKEN=$(gh auth token --user <bot-name>)` to authenticate as the bot.

Add the bot as a repo collaborator with write access. GitHub may grant
access directly (204) without creating an invitation — only accept if
one exists:

```bash
BOT_GH_TOKEN=$(gh auth token --user <bot-name>)
gh api "repos/$REPO/collaborators/<bot-name>" -X PUT -f permission=push
INVITE_ID=$(GH_TOKEN=$BOT_GH_TOKEN gh api "user/repository_invitations" --jq ".[] | select(.repository.full_name == \"$REPO\") | .id")
if [ -n "$INVITE_ID" ]; then
  GH_TOKEN=$BOT_GH_TOKEN gh api "user/repository_invitations/$INVITE_ID" -X PATCH
fi
gh api "repos/$REPO/collaborators" --jq '.[].login'
```

## 10. Bot profile bio

Capture what the creator is comfortable with contributors/users asking the
bot to do, then reflect that stance in the bot's profile bio (≤160 chars)
so it's discoverable on the bot's user page. This is advisory — the bot
doesn't gate behavior on it.

Ask the creator via `AskUserQuestion` which stance applies. Substitute
`<owner>/<repo>` at ask time. Order options recommended-first and mark
the recommended one explicitly:

- `tend agent for <owner>/<repo>. I triage issues and help maintain <repo>.` (Recommended — invites issue/PR engagement without inviting open-ended Q&A)
- `tend agent for <owner>/<repo>. Feel free to ask me questions about <repo>.` (Most permissive — invites contributor questions)
- `tend agent for <owner>/<repo>. I respond to maintainers of <repo>.` (Most restrictive — limits engagement to maintainers)

Check the current bio as the bot — skip if already set to the chosen value:

```bash
GH_TOKEN=$(gh auth token --user <bot-name>) gh api user --jq '.bio'
```

Otherwise write it (requires `user` scope on the bot's token from step 8):

```bash
GH_TOKEN=$(gh auth token --user <bot-name>) gh api user -X PATCH -f bio="<drafted bio>"
```

## 11. Commit and push

Stage all changes:

```bash
git add .
```

Commit with co-author attribution. Do NOT push without explicit permission.

After pushing the install PR, wait for the `tend-install-test` workflow
to pass before merging — it verifies the bot+harness secrets are set and
that the committed workflow files match the generator's output. The file
itself is removed on the next nightly regen, so future PRs won't trigger
it.

## Summary checklist

After completing all steps, present this checklist (harness-specific
line picks the row that matches the chosen harness):

- [ ] Config: `.config/tend.yaml` created (with `harness` set if Codex)
- [ ] Workflows: generated in `.github/workflows/`
- [ ] Rulesets: merge restriction on default branch (admin bypass), release tag operations (admin bypass)
- [ ] Release/deploy secrets: environment-protected; the environment's deployment-branch-policies list only the admin-gated refs from §3 (default branch and/or release tag pattern)
- [ ] Skill overlay: `.claude/skills/running-tend/SKILL.md` (tend-specific only)
- [ ] Badge: offered to add to README (optional)
- [ ] Bot account: `<bot-name>` exists on GitHub
- [ ] Harness auth (claude): `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` secret set
- [ ] Harness auth (codex): `CODEX_AUTH_JSON` (subscription, recommended) or `OPENAI_API_KEY` secret set
- [ ] Bot token: `TEND_BOT_TOKEN` secret set with `repo`+`workflow`+`notifications`+`write:discussion`+`gist`+`user` scopes
- [ ] Bot access: repo collaborator with write access, invitation accepted
- [ ] Bot bio: profile bio reflects the authorization stance
- [ ] Committed (push requires explicit permission)
- [ ] `tend-install-test` workflow passed on the install PR before merging
