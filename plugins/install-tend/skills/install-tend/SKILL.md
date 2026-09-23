---
name: install-tend
description: Installs tend, rotates credentials, repairs failing tend checks, and manages config, secrets, branch protection, and bot access.
---

# Install Tend

Set up tend on the current repo, or change an installation it already has.

When asking the user questions during these steps, batch known questions into
one interaction where the client supports it, and present concrete options
when there are clear choices (e.g. secret-migration confirmation, registry
token route).

When a question requires the user to do something off-screen (visit a URL,
run a command, paste a value back), spell the next step out in the question
or option description: the exact web link, the exact command. "Generate a
token on the registry's site" is not enough — give the URL. The user should
not have to ask "where do I do that?".

## Kickoff

Read `.config/tend.yaml` first. Its presence says whether this is an install or
a change to one, and where it exists it settles the harness: the `harness` key,
or Claude when the key is absent, which is how a Claude install is normally
written.

Derive `REPO` once at the start — the second call resolves a fork clone to
its root source, so no remotes need touching (every command below passes
`--repo "$REPO"` explicitly):

```bash
gh auth status
LOCAL=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
REPO=$(gh api "repos/$LOCAL" --jq 'if .fork then .source.full_name else .full_name end')
echo "$REPO"
```

When the resolution changes the name, or returns `null` (source deleted or
invisible to the token), confirm the target with the user before touching
anything — a deliberately maintained hard fork keeps its own name.

A config from a finished install makes this a change: take the harness from
the config, lay out only the steps the task touches, and start. Finished
means `uvx tend@latest check` passes — secrets, bot access, protection —
*and* the workflows are live on the default branch, which that check never
looks at (step 11 commits without pushing, so an install can stop with
everything else in place):

```bash
gh api "repos/$REPO/contents/.github/workflows" \
  --jq '[.[].name | select(startswith("tend-"))] | length'
```

The summary checklist at the end describes a finished install, so skip it.
A preference a step needs (7a's auth mode, 10's bio stance) is asked at
that step.

Otherwise this is an install, including a resume of one that never finished
(config present, later steps missing). Gather every preference at the
Kickoff, so the rest of the install stops only where a step genuinely
needs the user. First generate three bot-name candidates from the
bare repo name (`<repo>-bot`, `<repo>-tend`, `tend-<repo>`) and check their
availability in parallel:

```bash
for name in cand1 cand2 cand3; do
  gh api "users/$name" >/dev/null 2>&1 && echo "$name: TAKEN" || echo "$name: available"
done
```

Also check whether the repo has a README (names in step 5) — it decides
whether the badge appears in question 3 and the customize follow-up.

In the message alongside the questions, lay out the install: it targets
`$REPO`, runs the section headings below as steps, and typically takes 5–10
minutes of the user's hands-on time (browser logins, OAuth approvals,
occasional copy-paste) — the agent drives the rest, ending at a local
commit (pushing waits for their go-ahead, step 11).

Then ask these four questions together. Answering them is the
go-ahead — no separate "ready to start?" confirmation. Drop any question the
user's request or an existing config already answers (a supplied bot name, a
chosen harness; step 7 reads an existing auth mode from its secrets); a fully
specified request leaves nothing to ask, and is
itself the go-ahead.

1. **Harness** — which model runs the bot and which credential it
   bills to:
   - **Claude — OAuth token** (recommended for consumers with a Claude
     subscription) — draws from the subscription's usage limits.
   - **Claude — API key** — a console.anthropic.com key, billed per token.
     Fits when there's no subscription to draw on, or the user wants a
     dedicated billing surface and per-key revocation.
   - **Codex — Plus/Pro subscription** — experimental. It needs two browser
     handoffs: a Codex device approval and a repo-scoped GitHub token form.
     Concurrent jobs receive an access-only token; one serialized weekly
     workflow owns renewal. It depends on Codex's internal auth mode; detail in
     ${CLAUDE_SKILL_DIR}/references/security-model.md.
   - **Codex — OpenAI API key** — standard pay-per-token path.
2. **Merge mode** — who may merge into the default branch:
   - **Maintainer** (recommended) — the bot opens and updates PRs; only admins can
     update the default branch.
   - **Yolo** — the bot may merge ordinary PRs, but cannot push directly.
     Workflow and Tend-config changes require a fresh CODEOWNER approval;
     ask for the one maintainer GitHub user to own them. Teams are not accepted
     because Tend cannot prove the bot is not a member.
3. **Bot name** — the available candidates, recommended first. "Other"
   takes a custom name; check its availability before using it. The tool
   needs 2–4 options, so generate more candidates whenever fewer than two
   come back available.
4. **Defaults** — accept the default setup, or pick areas to change:
   - **Accept all defaults** (recommended) — no workflow overrides, a
     placeholder instructions overlay, the badge added, and the bot bio
     "tend agent for `<owner>/<repo>`. I triage issues and help maintain
     `<repo>`." Nothing is locked in: every default is an ordinary edit later
     (`.config/tend.yaml`, the files the install writes) or a re-run of
     this skill.
   - **Customize…** — pick the areas in a follow-up question.

A **Customize…** answer gets one more multi-select question: which areas to
change, defaults applying to whatever is left unselected (an empty submission
included), each option
naming its default in its description. Both the defaults description
and this follow-up list only the areas still open — drop an area the
user's request settles (the request, not the default, governs its step:
"skip the badge" skips it), an area a previous run already applied, and
an area that can't apply (no README → no badge option). The tool caps a
question at 4 options, so a new area means grouping, not appending.

- **Workflow config** — setup commands, workflow conditions, schedules, job
  permissions/timeouts, env vars (default: no overrides)
- **Bot instructions overlay** — PR title format, labels, review routing,
  target branch, nightly actions (default: a placeholder overlay)
- **README badge** — placement and style, or leaving it out (default:
  added, matching the README's existing badge style)
- **Bot profile bio** — the stance line on the bot's profile (default:
  "tend agent for `<owner>/<repo>`. I triage issues and help maintain
  `<repo>`.")

Each area selected there is asked about at its step (1, 4, 5, 10); the
rest apply the default without asking. Steps that can't be defaulted —
migrating a release secret, naming environment reviewers, creating the bot
account, approving OAuth — still interact when they arrive.

Follow each step in order. Skip steps that are already done — check each
prerequisite before acting.

Whatever the user has to act on — a URL, a code, a command — goes in the reply
itself, ready to use: a clickable link where the runtime renders one, and the
bare URL on its own line where they may need to copy it. Repeat it while its
window is still open.

## Browser sessions

Step 6 (when the bot account must be created), step 7b's Codex subscription
setup, and step 8's mint paths (8a/8b) need a browser session. Check whether
`mcp__claude-in-chrome__*` is connected (`tabs_context_mcp`) before the
first browser step, or any question that would offer one as an option.
When it is, drive the browser steps yourself rather than offering a
hand-off choice: hand the user only the prompts automation can't cross
(a signup CAPTCHA, a 2FA or password reauth), and resume once they
complete the prompt in the open tab. When it isn't but the runtime can open
a URL in the user's browser, open each URL as soon as it appears and hand over
the exact prompt or code. Otherwise, give the user the URL and wait for
confirmation.

Driving uses the user's real Chrome profile, so logging in as the bot
displaces their own github.com session until they sign back in — tell
them when handing the browser back. Before acting as the bot, verify
the logged-in user via the avatar menu.

## 1. Create config

Create `.config/tend.yaml` with at minimum `bot_name`, plus `harness` if
the user chose Codex (the default Claude harness can be omitted). See
README.md "Harnesses" for the comparison.

```yaml
bot_name: <bot-name>
# For autonomous merging (also set the owner selected at kickoff):
# merge: yolo
# control_plane_owner: "@maintainer"
# For Codex:
# harness: codex
# model: gpt-5.6-sol
# Both harnesses optionally accept:
# effort: medium   # low | medium | high | xhigh; Claude also accepts max
```

Write the Codex model into the config. It is the installation's reviewed pin;
the raw action deliberately has no model default.

List the secrets the repo already holds:

```bash
gh secret list --repo "$REPO" --json name --jq '.[].name'
```

Any repo-level secret not in `secrets.allowed` triggers a `tend check`
warning. The operational secrets (bot token, harness auth) never belong at
repo level — steps 7–8 store them in the `tend` environment, and a
repo-level copy from a pre-environment install is exactly the exposure the
environment closes, so it gets deleted once the environment copy is in
place. Classify each remaining secret and act now — don't defer:

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
  deployment policy pins to refs from §3 (all tags and the default branch).
  Under yolo, a generic credential environment admitting the default branch
  accepts that bot-merged code can use its credentials. Keep tag-only release
  credentials behind the all-tags ruleset or a non-bot required reviewer.
  `tend check` sweeps every credential-holding
  environment — one that stores a secret, or that an `id-token: write` job
  deploys to, since trusted publishing stores nothing — and fails on any it
  cannot confirm gated: no reviewer and no policy, an unverified branch
  entry, tag entries without §3's all-tags ruleset, or a ref policy on an
  environment some workflow reaches on `release`, `repository_dispatch`, or
  a `workflow_dispatch` with inputs, unless that policy admits yolo's
  default branch. A half-migrated environment surfaces on the next check
  rather than passing silently.

  Migrate the secret: recreate it on the Environment, delete the
  repo-level copy (confirm with the user first), and set
  `environment: <name>` on the publishing job.

  Configure the deployment policy. Allow whichever ref classes the
  workflow runs on:

  ```bash
  REPO=<owner>/<repo>; ENV=<name>
  DEFAULT_BRANCH=$(gh api "repos/$REPO" --jq .default_branch)
  gh api --method PUT "/repos/$REPO/environments/$ENV" \
    -F 'deployment_branch_policy[protected_branches]=false' \
    -F 'deployment_branch_policy[custom_branch_policies]=true'
  # Continuous-deploy on default branch:
  gh api --method POST "/repos/$REPO/environments/$ENV/deployment-branch-policies" \
    -f "name=$DEFAULT_BRANCH" -f type=branch
  # Release on tags (workflow has `on: push: tags:`):
  gh api --method POST "/repos/$REPO/environments/$ENV/deployment-branch-policies" \
    -f 'name=*' -f type=tag
  ```

  Verify:

  ```bash
  gh api "/repos/$REPO/environments/$ENV/deployment-branch-policies" \
    --jq '.branch_policies | map({name, type})'
  ```

  Each entry must match a ref class from §3 (default branch and/or all
  tags).

  Then sweep deploy/publish workflows. Each must trigger on `push: tags:`
  or `push: branches: [<default-branch>]` (per §3 workflow design) and
  declare an Environment. The grep below catches the common shapes; it
  misses reusable workflows in other repos and over-matches
  `pull_request_target` references in expressions and step inputs, so
  read each hit:

  ```bash
  grep -RniE 'tags:|workflow_dispatch|release:|schedule:|workflow_run|repository_dispatch|deployment:|pull_request_target' .github/workflows
  ```

  An OIDC-to-cloud deploy has no secret to migrate; the Environment with
  its admin-gated deployment policy plus the cloud provider's trust policy
  is then the only control on that path.

  The original repo-level secret value isn't readable (GitHub secrets are
  write-only), so a fresh token is needed. Ask the user
  how to obtain it; recommend whichever fits the registry:

  - **CLI** — if the registry has a token-issuing CLI (e.g., `npm token create`),
    run it and capture the token.
  - **Chrome** — drive the registry's token page via `mcp__claude-in-chrome`
    (most registries — PyPI, crates.io, Docker Hub — only issue tokens via
    the web UI). Some registries (PyPI in particular) force a 2FA reauth
    at token-creation time; the user completes it in the open tab, per
    Browser sessions.
  - **Manual** — user generates the token themselves on the registry's
    site and stores it themselves: hand over the environment's
    `gh secret set` command fully substituted. With neither `--body` nor
    a pipe it prompts for the value, so the token never sits in the chat
    transcript. Don't delete the repo-level copy until
    `gh secret list --repo "$REPO" --env "$ENV" --json name` shows it —
    the write is theirs on this route, so nothing else tells the agent it
    landed.

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

If the user picked workflow config at Kickoff, ask which overrides to set in
a multi-select question — otherwise set none:

- Setup commands and env vars (system deps, language version, pre-build
  hooks, top-level env vars; maintainer-mode `setup` accepts `run` and `uses` steps)
- Workflow conditions (e.g., skip review on `tend:dismissed` PRs — see below)
- Schedule overrides (cron timing for nightly/weekly)
- Permissions / timeouts on specific jobs

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
Merge Patch, the override must repeat the generated `TEND_ENABLED` pause check
(`init` refuses one that drops it):

```yaml
workflows:
  review:
    jobs:
      review:
        if: "vars.TEND_ENABLED != 'false' && !contains(github.event.pull_request.labels.*.name, 'tend:dismissed')"
```

See ${CLAUDE_SKILL_DIR}/references/tend.example.yaml for more override
examples (extending permissions, timeouts, top-level env vars).

## 2. Generate workflows

```bash
uvx tend@latest init --with-install-test
```

`--with-install-test` adds a one-shot `tend-install-test.yaml` workflow
that runs on the install PR to verify the committed workflows match the
generator's current output. (It cannot see secrets — its `pull_request`
run is outside the `tend` environment — so `tend check` is what verifies
those.) The next nightly regen
runs `uvx tend@latest init` without the flag, and the init cleanup step
removes the file from the default branch.

Verify workflow files appear in `.github/workflows/tend-*.yaml`.

Check for workflows using `anthropics/claude-code-action`:

```bash
grep -rl 'anthropics/claude-code-action' .github/workflows/ 2>/dev/null
```

If found, delete them — tend replaces claude-code-action entirely. Remind the
user that team members should @-mention the bot account instead of `@claude`.

## 3. Ref protection

Reconcile Tend's canonical rulesets and environment policy:

```bash
uvx tend@latest check --fix --repo "$REPO"
```

The command is expected to remain non-zero until later steps install the
secrets. Fix every ref-protection finding now. Under `maintainer`, `Merge access`
keeps the default and extra protected branches admin-only. Under `yolo`, it
targets only the default branch and grants the bot user a pull-request-only
bypass, while `Control-plane review` requires a fresh,
non-bypassable CODEOWNER approval for `.github/**` and
`.config/tend.yaml`, including the CODEOWNERS files and agent instructions.
`Protected branch access` keeps configured extra branches admin-only in yolo;
an existing copy is retained when returning to maintainer. `Tag operations`
keeps all tags admin-only.

Yolo bootstraps in two safe phases. Before the generated CODEOWNERS block and
exact generated workflows are on the default branch, or while any credential
check is unresolved, `--fix` keeps maintainer mode and refuses to grant the bot
a bypass. Merge the install PR manually and fix any credential gates, then
rerun this section; only then does it enable the pull-request-only bypass. Once
that bypass is active, an unresolved prerequisite makes `--fix` preserve the
live merge rules, and an inconclusive ruleset read prevents it from changing
them.
The workflow check also rejects non-generated jobs with dynamic environment
names or external or ref-qualified reusable workflows, because their use of
the `tend` environment cannot be verified from the default-branch tree.

Survey existing rulesets; skip any slot already covered:

```bash
gh api "repos/$REPO/rulesets" --jq '.[] | {name, target, enforcement}'
```

Read back what GitHub actually granted; repository role IDs are not ordered by
privilege, and hand-editing one can silently give the write-role bot an
always-bypass:

```bash
gh api graphql -f query='{repository(owner:"<owner>", name:"<repo>")
  {rulesets(first:10){nodes{name bypassActors(first:10)
  {nodes{repositoryRoleDatabaseId repositoryRoleName}}}}}}'
```

**Tag operations.** Same shape, applied to all tags. Pushing a new tag or
moving an existing one becomes an admin operation; the bot can do
neither. Skipping the "what pattern do your tags use?" question is
deliberate: matching all tags removes a per-repo configuration choice
and gives the chain a single, uniform rule.

```bash
gh api "repos/$REPO/rulesets" --method POST --input - << 'EOF'
{
  "name": "Tag operations",
  "target": "tag",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["~ALL"], "exclude": [] }
  },
  "rules": [
    { "type": "creation" },
    { "type": "update" }
  ],
  "bypass_actors": [{
    "actor_id": 5,
    "actor_type": "RepositoryRole",
    "bypass_mode": "exempt"
  }]
}
EOF
```

`creation` blocks the bot from pushing a fresh admin-gated tag; `update`
blocks rewriting an existing tag to point at a bot-controlled commit. The
chain doesn't need `deletion` separately. Recreation is already blocked
by `creation`, so a deleted tag can't be replaced with malicious code.
Bot-deleting an admin-pushed tag is brief availability damage at worst;
repos that need stronger protection against published-tag deletion can
add a no-bypass `deletion` ruleset (see the publisher uplift below).

**Immutable releases.** Enable this before the next release. It locks that
release's assets and its associated tag, but not its body — a write-access
actor can still edit the notes. GitHub does not apply the setting
retroactively. Reading the setting takes repository admin, so the nightly
`tend check` verifies the newest published release's own `immutable` flag —
on a repo that already had releases, that check fails until the next one:

```bash
gh api "repos/$REPO/immutable-releases" \
  -H 'X-GitHub-Api-Version: 2026-03-10' --method PUT
```

**Environment gates.** A new Environment admits every ref and requires no
approval — `deployment_branch_policy: null`, no reviewers — so a bot-pushed
branch or tag reaches its secrets and mints its OIDC token. Survey what
exists, including environments GitHub created on the repo's behalf
(`github-pages`) and ones that predate tend:

```bash
gh api "repos/$REPO/environments" \
  --jq '.environments[] | {name, deployment_branch_policy, rules: [.protection_rules[].type]}'
```

Each environment that holds a secret, or that a job with `id-token: write`
names, needs a policy pinned to refs verified under the selected merge mode
or required reviewers who exclude the bot. Under yolo, the default branch
qualifies: code the bot merges there may use generic credentials. A credential
that must stay beyond the bot's reach belongs on tags or extra protected
branches, or behind reviewers. Either gate clears
`credential-environments`, so an environment already behind reviewers
stays as it is.

Pin the policy to the refs its workflows actually use — all tags for a release,
and the default branch for a continuous deploy under either merge mode. In
yolo, a default-branch deploy can use its credential without another review:

```bash
gh api "repos/$REPO/environments/$ENV" --method PUT --input - << 'EOF'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api "repos/$REPO/environments/$ENV/deployment-branch-policies" \
  --method POST -f name='*' -f type=tag
```

`type` is `tag` or `branch` and defaults to `branch` when omitted, so a
tag entry that leaves it out silently protects nothing. Prefer this
explicit list to the "protected branches only" setting, which admits any
branch carrying a *classic* protection rule — a set that grows as
branches are created, and that excludes a branch protected by the
ruleset above.

Name required reviewers where no ref list fits — a deploy running from a
ref no policy can name, such as a preview published from
`refs/pull/N/merge`. Approval holds whatever ref the run starts from. Ask
which humans to name; the bot cannot be one of them:

```bash
ID=$(gh api "users/<login>" --jq .id)
gh api "repos/$REPO/environments/$ENV" --method PUT --input - << EOF
{"reviewers": [{"type": "User", "id": $ID}], "deployment_branch_policy": null}
EOF
```

**Release/deploy workflow design.** Workflows that use release or deploy
secrets must trigger on `push: tags:` (release) or `push: branches: [main]`
(continuous deploy from the default branch), and reference an Environment
(§1). Don't trigger on `pull_request`. A `pull_request` workflow runs the
YAML at the PR's head ref, which a bot can write, so the workflow code is
no longer admin-vetted and the chain breaks at the workflow file itself.
Triggers a write-scoped bot can fire *and* steer are outside the packaged
recipe: `release: published` (creating a release against an existing tag
takes no tag operation, and its body and assets are the bot's),
`repository_dispatch`, and a `workflow_dispatch` carrying inputs. Their
workflow files still run from the default branch, so the code is reviewed
under maintainer mode or subject to yolo's control-plane rules, but the bot
chooses when they fire and what payload they
see. If a repo keeps one on a release/deploy workflow, gate that
Environment with required reviewers before migrating release or deploy
secrets to it, unless the policy admits yolo's default branch and that
exposure is intentional.

Run `uvx tend@latest check` after this section. It exits non-zero until
the later steps set the secrets and grant the bot access; read its
`credential-environments` line, which reports any environment still
reachable by the bot.

**More complicated approaches are possible** (per-pattern tag rulesets,
mixed bypass actors, layered no-bypass immutability rulesets for repos
that publish actions consumed via tag pins). Install-tend packages the
recipe above because it is the simplest configuration that holds the chain;
consumers with stricter requirements can layer additional rulesets or
environment protection rules on top.

## 4. Create skill overlay (recommended)

Create `.claude/skills/running-tend/SKILL.md` with tend-specific project
instructions, opening with the frontmatter below so discovery lists it by
description rather than by its first heading. An existing overlay without
frontmatter needs it added in place.

**Do not create a second independent copy of project instructions** and **do
not invent project conventions.** If the repo has only one of `CLAUDE.md` or
`AGENTS.md`, create a relative symlink at the other name so both harnesses read
the same content. Preserve both when both already exist, and create neither
when neither exists:

```bash
if [ -f CLAUDE.md ] && [ ! -e AGENTS.md ] && [ ! -L AGENTS.md ]; then
  ln -s CLAUDE.md AGENTS.md
elif [ -f AGENTS.md ] && [ ! -e CLAUDE.md ] && [ ! -L CLAUDE.md ]; then
  ln -s AGENTS.md CLAUDE.md
fi
```

If the user picked the overlay at Kickoff, ask which tend-specific
preferences to capture in a multi-select question:

- PR conventions (title format — e.g., conventional commits, Jira ticket
  prefix — and labels the bot should apply)
- Review request routing (specific teams or people)
- Target branch if not the default branch
- Optional nightly actions (e.g., changelog maintenance — specify file and branch)

For each selected item, follow up with a free-text ask to capture the
specifics, then write them into the overlay. Otherwise create a
placeholder:

```markdown
---
name: running-tend
description: Project-specific instructions for tend workflows running on this repo.
---

No project-specific tend preferences yet. Add instructions here as
needed — this file is loaded by tend workflows alongside the project's
instruction file.
```

Build commands, test commands, code style, and project structure belong in
the project's `CLAUDE.md` or `AGENTS.md`; Tend reads the applicable project
instructions like any other agent session.

## 5. README badge

If the repo has a README (any of `README.md`, `README.rst`, `README`), add a
"maintained with tend" badge. If the user picked the badge at Kickoff, first
ask what they want (skip it, or a placement/style preference); otherwise
insert it without asking.

Base URL (always include the logo):

```
https://img.shields.io/badge/maintained_with-tend-bba580?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwxNikgc2NhbGUoMC4wMTI1LC0wLjAxMjUpIiBmaWxsPSIjZmZmIiBzdHJva2U9Im5vbmUiPjxwYXRoIGQ9Ik02ODAgMTEyOCBjNjIgLTk2IDY5IC0xNzggMjAgLTI0MSAtMTcgLTIyIC0yMCAtNDAgLTIwIC0xMzQgbDEgLTEwOCAyMSAyOCBjMTEgMTYgMzAgNDcgNDIgNzAgMTIgMjIgMzIgNDkgNDYgNTkgMzcgMjcgMTE0IDM4IDE4NCAyNyA5MyAtMTUgOTQgLTE4IDQ0IC03OSAtNzIgLTg4IC0xMDkgLTExMyAtMTc2IC0xMTcgLTMxIC0yIC02NCAxIC03MiA2IC0yMyAxNSAyMSA1NiAxMDcgOTggNDAgMjAgNzEgMzggNjkgNDAgLTYgNyAtODggLTE3IC0xMjYgLTM3IC00OSAtMjUgLTEwMCAtNzggLTEyMSAtMTI1IC0xNSAtMzMgLTE5IC02NiAtMTkgLTE4OCAwIC0xNTcgOCAtMTk1IDUwIC0yMzIgMTcgLTE2IDM2IC0yMCA4NSAtMTkgNjIgMSA2MyAxIDczIC0zMiA5IC0zMiA5IC0zMyAtMjIgLTQwIC01MCAtMTIgLTEzMiAtNyAtMTY0IDEwIC00MCAyMSAtNzkgNjkgLTkyIDExNCAtNSAyMCAtMTAgMTAyIC0xMCAxODIgMCA4MCAtNSAxNjIgLTExIDE4NCAtMjIgNzkgLTEzNSAxNjYgLTIzNCAxODEgLTM3IDYgLTM1IDMgMzAgLTI4IDc4IC0zOSAxNDQgLTkxIDEzMiAtMTA0IC01IC00IC0zNyAtOCAtNzEgLTggLTc3IDAgLTExNyAyNCAtMTgyIDEwOSAtNTIgNjggLTUxIDcwIDQyIDg1IDcxIDExIDE0MyAwIDE4MyAtMjkgMTYgLTExIDQwIC00MyA1NCAtNzMgMTMgLTI5IDMyIC01OSA0MSAtNjYgMTQgLTEyIDE2IC03IDE2IDU4IDAgNTkgNCA3NyAyMyAxMDIgMTkgMjYgMjMgNDYgMjUgMTMwIDMgNjcgMCA5OSAtNyA5OSAtNyAwIC0xMSAtMjMgLTEyIC01NyAwIC0zMiAtNiAtNzYgLTEyIC05NyBsLTEyIC00MCAtMjcgMzIgYy0zNCA0MSAtNDMgOTYgLTI0IDE1MSAxNCA0MSA3NSAxNDEgODYgMTQxIDMgMCAyMSAtMjQgNDAgLTUyeiIvPjwvZz48L3N2Zz4K
```

Match the `style` parameter used by existing badges in the README. For
example, if the repo uses `style=for-the-badge`, append
`&style=for-the-badge` to the URL. If no existing badges or no style
parameter, use the default (no style parameter needed).

Wrap the image in a link to `https://github.com/max-sixty/tend` — always
this exact URL, regardless of the consumer's org or repo name. The full
markdown shape:

```
[![maintained with tend](<image-url>)](https://github.com/max-sixty/tend)
```

Wherever the badge comes up in chat (the Kickoff defaults description,
the customize follow-up),
describe it briefly ("an olive-green 'maintained with tend' badge with the
tend wordmark") — do NOT paste the raw `img.shields.io` URL or its base64
logo blob into the chat; the blob is hundreds of characters of noise.

Place it near the top of the README — after the title/heading but
before the first paragraph. If there are already badges on that line,
append to the same line.

## 6. Bot account

```bash
gh api users/<bot-name> --jq '.login,.id' 2>/dev/null && echo "EXISTS" || echo "NOT FOUND"
```

If the account doesn't exist (the name comes from Kickoff or
`bot_name` in the config):

1. Ask for the bot's email address. It must be one the user can read —
   the verification code lands there; a plus-alias of their own address
   (`user+<bot-name>@…`) works.
2. Navigate Chrome to `https://github.com/signup` and fill the form.
   The user types the password and solves the CAPTCHA — the password
   stays out of the conversation like any secret. Have them save it;
   later bot logins (device-flow approvals, scope refreshes) need it.
3. If a verification code is needed and an email-reading skill or MCP is
   available, use it to fetch the latest GitHub verification email
   (`from:github subject:code`); otherwise have the user paste the code.
4. After confirmation, re-verify via API.

## 7. Harness auth token

This step and step 8 store their secrets in the `tend` GitHub
Environment — a repo-level secret is readable by any workflow the repo
runs, including one pushed to a branch, and the environment's deployment
policy is what closes that. `tend check --fix` owns the environment's
shape (it creates it and admits exactly the default branch and each
`protected_branches` entry), so create it with:

```bash
uvx tend@latest check --fix
```

It exits non-zero here, reporting the secrets this step and step 8 are
about to set. Read its `environment` line: that is the one that must pass.

A repo-level copy of an operational secret (from a pre-environment
install) can't be read back — GitHub secrets are write-only — so mint the
value into the environment per the steps below, then delete the
repo-level copy; `tend check` flags it until deleted.

List the environment secrets and their `Updated` times before changing auth:

```bash
gh secret list --repo "$REPO" --env tend
```

For a harness credential rotation that names no credential, keep the active
mode: Claude prefers its OAuth secret over its API key; Codex prefers
`CODEX_AUTH_JSON` over `OPENAI_API_KEY`. When the user names a credential,
rotate that one and distinguish it from a switch of the active mode. Mint
a new value before writing the secret, then verify its `Updated` time
advances. For harness credential rotation, invalidate the old value at its
issuer after the new one is stored; an updated secret does not revoke it.
If its issuer offers no verified revocation path, report that the old
credential may still work instead of calling the rotation complete. A
secret's presence alone finishes only an initial setup.
Where the user runs `gh secret set` themselves, re-run the listing once
they say they're done.

Branch on the harness.

### 7a. Harness = claude

If `CLAUDE_CODE_OAUTH_TOKEN` exists, it is the active mode; otherwise use
`ANTHROPIC_API_KEY` if present. For rotation, mint a new credential in that
mode. If neither secret exists, use the mode chosen at Kickoff or ask the
user to choose between the two Claude options from Kickoff question 1.

For **OAuth token** (`sk-ant-oat01-…` from `claude setup-token`; advertised
as 1-year), the user mints it. Hand over both commands, fully substituted,
for their own terminal (any machine with Claude Code installed and `gh`
logged in as the maintainer; `https://claude.com/claude-code` to install
it), whenever suits them:

```bash
claude setup-token
```

```bash
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo "$REPO" --env tend
```

Offer both endings when handing the commands over: run `gh secret set`
themselves so the value never leaves their terminal, or paste the token back
for the agent to set it. Recommend the first and say why — a token pasted
into chat is a live credential in a transcript that outlives the install.
Given neither `--body` nor a pipe, `gh secret set` prompts for the value, so
taken that way the token goes from `claude setup-token`'s output to the
prompt and nowhere else.

When setting it from a pasted value, check it starts with `sk-ant-oat01-`
first. `gh secret set` stores an empty or malformed body and exits 0, and
every check downstream reads names rather than values (`check_secrets`, and
the listing above), so a bad one reads as already set on the
next run, which skips it and finishes green.

Either way, give them this alongside, which shows the secret's name and when
it was written without exposing the value:

```bash
gh secret list --repo "$REPO" --env tend
```

For rotation, have the user identify the old Claude Code authorization
in `https://claude.ai/settings/claude-code` before minting the replacement,
then revoke that old authorization there after the secret is updated.

For **API key**: the user takes a key from
`https://console.anthropic.com/settings/keys` and runs this themselves,
substituted, pasting the key at the prompt:

```bash
gh secret set ANTHROPIC_API_KEY --repo "$REPO" --env tend
```

For rotation, have the user identify the old key before creating the new
one, then delete the old key from the console after updating the secret.

### 7b. Harness = codex

If `CODEX_AUTH_JSON` exists, subscription auth is active; otherwise use
`OPENAI_API_KEY` if present. For rotation, provision a new credential in
that mode. If neither secret exists, use the mode selected at Kickoff or
ask the user to choose between the two Codex options there.

For **Plus/Pro subscription**, explain that the path is experimental because it
depends on Codex's internal auth mode. Use an isolated Codex login: the weekly
workflow will rotate its refresh-token chain, so copying the user's ordinary
`~/.codex/auth.json` would eventually break their local Codex login.

Run the bundled provisioner yourself. The user approves the device login in
their browser; they do not run commands or handle the resulting Codex
credentials. Start it in the background, surface the URL and one-time code from
its output, and keep reading until it exits:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/install_codex_subscription_auth.py" provision --repo "$REPO"
```

The provisioner uses `codex`, or `npx -y @openai/codex@latest` when the CLI is
absent. It validates and splits the login, stores both GitHub secrets without
printing them, verifies the secret names, and removes its temporary Codex home.

For rotation, this installs a replacement but cannot itself revoke the old
Codex subscription login: the previous `auth.json` is removed and GitHub
secrets cannot be read back. Tell the user the old credential may remain
valid, have them check their ChatGPT account's security settings for a
session-revocation control, and do not report the rotation complete.

The serialized refresh workflow also needs a fine-grained PAT scoped only to
`$REPO`, with repository permission **Environments: Read and write**;
`GITHUB_TOKEN` cannot replace environment secrets. GitHub does not provide an
API for minting this PAT. Open a prefilled token form, then select **Only select
repositories** and `$REPO`; the user handles any password or 2FA prompt and
clicks **Generate token**, then **Copy**:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/install_codex_subscription_auth.py" pat-url --repo "$REPO"
```

When the browser and shell share a clipboard, pipe the copied token to the
provisioner without displaying it. Use the host's clipboard reader; on macOS:

```bash
pbpaste | python3 "${CLAUDE_SKILL_DIR}/scripts/install_codex_subscription_auth.py" store-pat --repo "$REPO"
```

The provisioner validates the fine-grained-token prefix, stores the secret,
and verifies all three subscription secret names. If no shared clipboard is
available, have the user run `gh secret set
CODEX_REFRESH_PAT --repo "$REPO" --env tend` in their own terminal and paste
the token at its hidden prompt. Never ask them to paste it into chat. Finish
only after all three secret names appear in the environment's secret listing.

For **API key**, the user takes a key from
`https://platform.openai.com/api-keys` and runs this themselves, pasting it at
the prompt:

```bash
gh secret set OPENAI_API_KEY --repo "$REPO" --env tend
```

For rotation, have the user identify the old key before creating the new
one, then delete the old key from the platform after updating the secret.

## 8. Bot token and secret

The bot's token needs scopes `repo`, `workflow`, `notifications`,
`write:discussion`, `gist`, and `user` (per-scope justifications in
${CLAUDE_SKILL_DIR}/references/tend.example.yaml).

This step checks what gh already stores for the bot, mints a token for
missing auth, missing scopes, or requested bot PAT rotation (8a or 8b),
and pushes it to the environment secret (8c). It serves both the install
sequence and a standalone `Bot PAT`
scope-audit remediation; in the audit case it is the whole fix, and
you close the issue once 8c verifies. `<bot-name>` is `bot_name` in
`.config/tend.yaml`; `$REPO` derives as in the Kickoff (whose recipe
resolves a fork clone to the canonical repo).

Bot auth lives in a dedicated config dir,
`$HOME/.config/gh-bots/<bot-name>`, with the token stored plaintext
(mode 0600) via `--insecure-storage` — never the OS keychain, which gh
keys by account name globally, so a keychain-backed bot login could
overwrite the maintainer's own credential. The bot also never enters
the default config, which git's gh credential helper answers as, so a
stray `git push` can't land as the bot. The token is already an Actions
secret, so the on-disk copy adds no exposure. The dir is durable:
scope audits and reinstalls read it to skip a fresh device flow. Full
rationale: ${CLAUDE_SKILL_DIR}/references/security-model.md.

Three auth postures, one per command — never export a token for the
session (git's gh helper would forward it):

- **Bot dir** (`gh auth …`, `gh api user`): prefix with
  `env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>"`.
  Ambient env tokens otherwise hijack the reads and block the auth
  writes. After verifying the bot identity, read its active token with
  `gh auth token` without `--user`; the named lookup can return an older
  keyring token for the same login.
- **Bot via token** (`GH_TOKEN=$BOT_GH_TOKEN gh api …`): each block
  reads the token, then skips its action if the read came back empty —
  an empty `GH_TOKEN` silently falls back to the maintainer's stored
  auth, and `gh secret set` accepts an empty body. Guards skip rather
  than `exit` because blocks may be pasted into the user's shell.
- **Maintainer** (`gh secret`, the collaborator API in step 9): bare,
  on ambient auth. A 403 means that auth lacks admin — often a weak
  env token; `env -u` it to fall back to the stored login.

No step writes the maintainer's default config — and the bot must not
sit there either (pre-dir installs put it there). If a bare
`gh auth token --user <bot-name>` prints a token, evict it with
`env -u GH_TOKEN -u GITHUB_TOKEN gh auth logout --user <bot-name>`;
workflows run on the repo secret, so nothing breaks.

Check what the bot dir holds:

```bash
env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" gh auth status 2>&1
```

A missing dir prints "not logged in", which is a routing answer, not an
error to debug. Read the output:

- Rotating the bot PAT while logged in as `<bot-name>` → have the user sign
  in as the bot at `https://github.com/settings/applications` and revoke
  **GitHub CLI** under Authorized OAuth Apps, then take the **refresh
  path** (8a), even if all six scopes are present. `gh auth refresh` does
  not revoke the old token.
  Revoking this authorization invalidates the bot's other GitHub CLI tokens
  too; update any other installation using that bot account.
- Logged in as `<bot-name>` with a `Token scopes:` line listing all six
  scopes, with no rotation requested → skip to 8c.
- Logged in as `<bot-name>`, scopes missing → **refresh path** (8a).
- Not logged in here → **login path** (8b).

8a and 8b both run gh's device flow: the command prints a one-time code
and polls until it is approved at `https://github.com/login/device` by
a browser logged in as the bot (codes expire after ~15 minutes). Run
the command yourself in the background, surfacing the code and URL;
delegate to the user's terminal only if that fails, and then hand over
the rest of the step's commands (8c, steps 9 and 10) fully
substituted — the token lives on whichever machine ran the login. On
Windows, run everything in Git Bash (bundled with Git for Windows); the
snippets work unmodified.

### 8a. Refresh path (bot already in the bot dir)

```bash
env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" \
  gh auth refresh --hostname github.com --insecure-storage \
  --scopes repo,workflow,notifications,write:discussion,gist,user
```

`gh auth refresh` has no `--user` flag; it operates on the dir's active
account, the bot. Requested scopes merge with the stored token's while
it is still valid (a revoked one yields just the six). No identity
check is needed: a wrong-session approval makes refresh itself error
("received credentials for <other-user>") — have the user re-approve
from the bot's session and rerun.

### 8b. Login path (first-time setup)

```bash
env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" \
  gh auth login --hostname github.com --web \
  --insecure-storage \
  --scopes repo,workflow,notifications,write:discussion,gist,user
```

No `--git-protocol` here: that flag writes gh's credential helper into
the global git config, host-wide (git config is not scoped by
`GH_CONFIG_DIR`).

`gh auth login` stores whatever account approved the code, without
checking. Verify before continuing:

```bash
env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" gh api user --jq '.login'
```

This must print the bot name. Anything else means the wrong session
approved the code; the token never left the bot dir, so delete the dir
and rerun 8b before proceeding — no other account is affected:

```bash
rm -rf "$HOME/.config/gh-bots/<bot-name>"
```

### 8c. Push token to secret

Copy the bot's token to the `TEND_BOT_TOKEN` environment secret and verify
the `Updated` timestamp advances. For rotation, complete 8a first: this
timestamp proves the secret was written, not that the PAT changed.

```bash
BOT_GH_TOKEN=$(env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" gh auth token)
BOT_LOGIN=$(GH_TOKEN=$BOT_GH_TOKEN gh api user --jq '.login' 2>/dev/null)
if [ -z "$BOT_GH_TOKEN" ] || [ "$BOT_LOGIN" != "<bot-name>" ]; then
  echo "bot token missing or no longer valid — redo 8a (8b if the bot dir is empty)" >&2
else
  gh secret set TEND_BOT_TOKEN --repo "$REPO" --env tend --body "$BOT_GH_TOKEN"
  gh secret list --repo "$REPO" --env tend
fi
```

## 9. Grant bot access

The collaborator PUT and final list run as the maintainer (they need
admin); accepting the invitation runs as the bot. GitHub may grant
access directly (204) without creating an invitation — accept only if
one exists.

```bash
BOT_GH_TOKEN=$(env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" gh auth token)
if [ -z "$BOT_GH_TOKEN" ]; then
  echo "bot token empty — fix step 8 first" >&2
else
  gh api "repos/$REPO/collaborators/<bot-name>" -X PUT -f permission=push
  INVITE_ID=$(GH_TOKEN=$BOT_GH_TOKEN gh api "user/repository_invitations" --jq ".[] | select(.repository.full_name == \"$REPO\") | .id")
  if [ -n "$INVITE_ID" ]; then
    GH_TOKEN=$BOT_GH_TOKEN gh api "user/repository_invitations/$INVITE_ID" -X PATCH
  fi
  GH_TOKEN=$BOT_GH_TOKEN gh api "repos/$REPO/subscription" -X PUT \
    -F subscribed=true -F ignored=false --jq '{subscribed, ignored}'
  gh api "repos/$REPO/collaborators" --jq '.[].login'
fi
```

## 10. Bot profile bio

Capture what the creator is comfortable with contributors/users asking the
bot to do, then reflect that stance in the bot's profile bio (≤160 chars)
so it's discoverable on the bot's user page. This is advisory — the bot
doesn't gate behavior on it.

Use the recommended stance below unless the user picked the bio at Kickoff or
there was no Kickoff round (a change flow) — then ask which applies. Substitute
`<owner>/<repo>`. Order options recommended-first and mark the recommended one
explicitly:

- `tend agent for <owner>/<repo>. I triage issues and help maintain <repo>.` (Recommended — invites issue/PR engagement without inviting open-ended Q&A)
- `tend agent for <owner>/<repo>. Feel free to ask me questions about <repo>.` (Most permissive — invites contributor questions)
- `tend agent for <owner>/<repo>. I respond to maintainers of <repo>.` (Most restrictive — limits engagement to maintainers)

Check the current bio as the bot — skip the write if it already matches:

```bash
BOT_GH_TOKEN=$(env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" gh auth token)
if [ -z "$BOT_GH_TOKEN" ]; then
  echo "bot token empty — fix step 8 first" >&2
else
  GH_TOKEN=$BOT_GH_TOKEN gh api user --jq '.bio'
fi
```

Otherwise write it (requires `user` scope on the bot's token from step 8):

```bash
BOT_GH_TOKEN=$(env -u GH_TOKEN -u GITHUB_TOKEN \
  GH_CONFIG_DIR="$HOME/.config/gh-bots/<bot-name>" gh auth token)
if [ -z "$BOT_GH_TOKEN" ]; then
  echo "bot token empty — fix step 8 first" >&2
else
  GH_TOKEN=$BOT_GH_TOKEN gh api user -X PATCH -f bio="<drafted bio>"
fi
```

## 11. Verify, commit and push

Everything `check` inspects is in place by now, so this run must pass:

```bash
uvx tend@latest check
```

A failure here is a real one — fix it before committing.

Stage all changes:

```bash
git add .
```

Commit with co-author attribution. Do NOT push without explicit permission.

After pushing the install PR, wait for the `tend-install-test` workflow
to pass before merging — it verifies that the committed workflow files
match the generator's output, the one thing a `pull_request` run can see.
Merging is the user's call. The file itself is removed on the next
nightly regen, so future PRs won't trigger it.

## Summary checklist

After completing all steps, present this checklist (harness-specific
line picks the row that matches the chosen harness):

- [ ] Config: `.config/tend.yaml` created (with `harness` set if Codex)
- [ ] Workflows: generated in `.github/workflows/`
- [ ] Rulesets: merge mode on the default branch, extra protected branches admin-only, tag operations admin-only; yolo also has control-plane CODEOWNERS review
- [ ] Immutable releases: enabled before the next release
- [ ] Release/deploy credentials: environment-protected; policies list only verified refs, with default-branch credentials deliberately reachable by bot-merged code in yolo
- [ ] Skill overlay: `.claude/skills/running-tend/SKILL.md` (tend-specific only)
- [ ] Project instructions: `CLAUDE.md` and `AGENTS.md` share one source when only one existed before install
- [ ] Badge: added to README (unless skipped, or no README)
- [ ] Bot account: `<bot-name>` exists on GitHub
- [ ] Harness auth (claude): `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` secret set
- [ ] Harness auth (codex): `OPENAI_API_KEY`, or all of `CODEX_AUTH_JSON` + `CODEX_REFRESH_AUTH_JSON` + `CODEX_REFRESH_PAT`
- [ ] Bot token: `TEND_BOT_TOKEN` set with `repo`+`workflow`+`notifications`+`write:discussion`+`gist`+`user` scopes
- [ ] Bot access: repo collaborator with write access, invitation accepted
- [ ] Bot notifications: watching the repository
- [ ] Bot bio: profile bio reflects the authorization stance
- [ ] `uvx tend@latest check` passes
- [ ] Committed (push requires explicit permission)
- [ ] `tend-install-test` workflow passed on the install PR before merging
