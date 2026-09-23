# CI Automation Security Model

Tend gives an AI agent write access to a repository and runs it on
attacker-controlled input (PR diffs, issue bodies, comments, CI logs). The
agent uses authenticated GitHub and model connections to push commits, post
reviews, and create PRs. The security model keeps the PAT and long-lived model
credentials outside the agent process. Merge authority is explicit: the
default mode requires a maintainer, while yolo lets the bot merge ordinary
code but keeps the repository control plane maintainer-owned. The agent is expected to use the GitHub
API for any repository the bot account can access, including repositories
other than the one that started the run.

Each consumer repo should document its specific configuration (admin accounts,
token names, protected environments) in its own
`.claude/skills/running-tend/SKILL.md`, the consumer-owned overlay the rest of
the docs name. Not a `docs/agent-notes.md` of its own: PR instruction
pinning covers `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`,
`AGENTS.override.md`, `.claude/`, and `.agents/` at any depth under both
harnesses (`shared/steps/restore-sensitive-config.sh`), so notes parked outside those
paths are read from the PR's own tree.

## Threats

Three things an attacker wants, roughly in order of severity:

1. **Merge malicious code to the default branch.** Game over — the attacker
   controls the repo. Everything else is damage limitation compared to this.

2. **Exfiltrate tokens.** Code running inside the agent must cross the UID
   boundary or compromise a runner-owned proxy to steal the bot PAT or API
   credentials. Subscription consumers do receive an expiring access token,
   but never its rotating refresh token. A stolen PAT grants persistent GitHub
   access; stolen model auth grants billed model access.

3. **Hijack a single session.** An attacker who controls what the agent does
   in one run can push malicious branches, post misleading reviews, or create
   spam PRs.

The attack surface varies by workflow. `tend-review` is the most exposed —
the attacker controls the entire PR diff, which Claude reads and reasons
about. `tend-weekly` is the least exposed — triggered on a cron with no
user-controlled input.

The merge rulesets, environment gates, credential isolation, and fixed
prompts apply to every workflow; the table lists what is specific to each.

| Workflow | Injection surface | Attacker control | Specific mitigations |
|----------|-------------------|-------------------|-------------|
| **review** | PR diff content, review body on bot PRs | Full (any PR) / Medium (reviewers) | Base-branch config restoration |
| **triage** | Issue body | Partial (structured skill) | Structured skill |
| **mention** | Comment body on any issue/PR | Full | Engagement verification; review events re-entered via a secretless relay |
| **ci-fix** | Unsuccessful CI logs | Minimal (must disrupt CI on default branch) | Automatic trigger |
| **weekly** | None | None | Scheduled trigger |

## What we do

Three load-bearing boundaries, with one deliberate policy choice:

1. **Merge authority is explicit.** Under the default `maintainer` mode, the
   bot cannot update the default branch. Under `yolo`, it can merge ordinary
   PRs but cannot push directly; changes to `.github/**` or
   `.config/tend.yaml` still need fresh CODEOWNER approval. Extra protected
   branches and tags remain admin-only in both policies.
2. **Tend's operational credentials stay out of bot-controlled code.** Tend
   verifies the exact generated workflows, reserves its environment for them,
   and rejects other workflows whose environment use is dynamic or hidden in
   an external or ref-qualified reusable workflow. Their harness isolates
   the long-lived credentials from agent code. Generic credentials are gated
   on bot-inaccessible refs in maintainer mode. Yolo deliberately accepts
   their exposure to code the bot merges to the default branch; a deployment
   from that branch needs no additional reviewer unless its workflow accepts
   a payload the bot can steer. Credentials restricted to
   tags or extra protected branches keep their ref gates.
3. **Future releases' assets and tags cannot be rewritten.** GitHub immutable
   releases lock a published release's assets and its associated tag from
   the point the repository setting is enabled. The release's body is not
   locked: a write-access actor can still edit the notes of an immutable
   release, verified against live GitHub with a write-scoped token.

`tend check` fails until the first two hold and the third is enabled, so a
passing check by a repository admin *is* the claim for the assets and tag of
every release published afterwards. GitHub does not apply the setting
retroactively.

A run below admin cannot read the setting and reads the newest published
release's `immutable` flag instead, which is retrospective: it establishes that
the setting was enabled when that release was published, not that it is enabled
now. Turning the setting off is therefore invisible to the nightly run until
the repository publishes again — at which point the check fails. Closing that
window takes an admin-run `tend check`.

**Merge rulesets.** In `maintainer`, `Merge access` protects the default branch
and configured `protected_branches` with an admin-only bypass. Reconciliation
preserves its existing branch targets and exclusions; it also leaves any
admin-only `Protected branch access` ruleset from an earlier yolo setup in place.
In `yolo`, `Merge access` targets only the default branch and grants the bot
GitHub's `pull_request` bypass mode: it may update that branch through a PR but
not by direct push. `Protected branch access` protects configured extra branches
with an admin-only bypass. Both rulesets protect ref creation, updates, and
deletion, so a bot cannot delete and recreate an admitted ref. Before granting
the yolo bypass, `tend check --fix` refuses to retire existing protected refs:
a maintainer must first remove or independently gate their access to credential
environments, then manually retire their ruleset targets. This keeps a mode
change or failed reconciliation from exposing secrets through a newly writable
branch.
The composite action verifies the bot's exact effective answer from
`current_user_can_bypass`: `never` for maintainer, `pull_requests_only` for yolo.
Required reviews alone do not qualify because the bot's own approval counts on
another author's PR. If GitHub cannot answer the ruleset read, maintainer
preflight settles for the branch-protected floor; yolo fails closed because it
must verify the exact middle state.

Yolo adds `Control-plane review`, a default-branch pull-request rule that the
bot cannot bypass. `tend init` puts a managed block last in the effective
CODEOWNERS file, assigning `/.github/**`, `/.config/tend.yaml`, and every
possible CODEOWNERS location to `control_plane_owner`. The block also covers
every `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `AGENTS.override.md`,
`.claude/`, and `.agents/` path because these files steer later agent runs. The owner must be one
maintainer GitHub user distinct from the bot. Protecting the ownership files
themselves prevents the bot from replacing the effective one before changing
another control-plane path. GitHub therefore admits ordinary PRs with zero
blanket approvals, but requires a fresh owner approval for control-plane
changes. Stale approvals are dismissed on push. Tend enables the
yolo bypass only after `tend check` sees that CODEOWNERS block on the default
branch. The exact-workflow check reconstructs the same repository owner,
default branch, and config path that `tend init` used, so it compares the files
against the output that belongs in this repository rather than generic
generator defaults.

**Environment-gated secrets.** A job that names a GitHub Environment runs
only if the run's `GITHUB_REF` matches the environment's deployment branch
policy; otherwise the job is refused before its first step, and the
environment's secrets are released only to jobs that name it. Pinning the
policy to authorized refs therefore decides secret access by ref. In maintainer
mode those are refs the bot cannot move. In yolo, Tend's own environment also
admits the default branch because generated workflows run there; its
operational credentials remain protected by the control-plane rule and
harness isolation. A generic environment may admit the default branch without
a reviewer unless a workflow reaching it accepts a payload the bot can steer;
the policy accepts that bot-merged code may reach its credentials. Tags
remain admin-only, and managing environments requires admin, which the bot
lacks.

The credential boundary is the conjunction of three checks, each keyed on
where a credential can live:

- *Every generic credential-holding environment follows the merge policy*
  (`credential-environments`): a required reviewer who is not the bot, or
  a deployment policy naming only branches verified under that mode and tags
  under an admin-only all-tags ruleset. A workflow reached on a trigger the
  bot steers also needs a reviewer, including when its policy admits yolo's
  default branch. Yolo's verified default branch is intentionally
  writable through pull requests; extra protected branches are not. This
  covers release tokens keyed on holding a credential rather than on any
  environment name. A credential
  is a stored secret, or the OIDC token a job minting `id-token: write`
  in the environment's name can spend: trusted publishing (PyPI, npm, a
  cloud role) stores nothing, so a sweep reading stored secrets alone
  walks past exactly the repos that publish.
- *No repo-level secret outside the allowlist* (`repo-secret-allowlist`):
  a repo-level secret is readable by any workflow the repo runs, so each
  one must be a deliberate `secrets.allowed` entry — and the operational
  names are refused there at config load, so no one config line can
  reopen the gate. Org-level secrets are swept into the same check
  best-effort: they cannot be environment-gated at all, and listing them
  needs `admin:org`, the one place the claim rests on the token the
  maintainer ran `tend check` with. Only the org secrets this repo can
  actually read count — one scoped away from it (`selected` without the
  repo, `private` against a public repo, any of them against a private
  repo in a GitHub Free org) reaches no workflow here, and naming it
  would leave a failure no repo-side change can clear. A secret whose
  reach cannot be determined is reported rather than dropped.
- *The operational secrets actually live in the gated environment*
  (`environment`, `secrets`): the `tend` policy admits exactly the
  verified branches, and the bot PAT and harness auth are present there
  rather than anywhere flatter.

What a pushed workflow holds, then, is only what GitHub gives every run:
its ephemeral `GITHUB_TOKEN`, at whatever permissions the file declares —
bounded by the repository rulesets, unable to read any secret value back
through the API, and expiring with the job. Yolo's PR-only bypass belongs to
the PAT-authenticated Tend bot instead: it can merge ordinary code, but
control-plane paths still require a CODEOWNER and tags remain admin-only.

*Operational secrets* — the bot PAT, harness auth, the Codex subscription
refresher's credential, and optional auto-memory Gist ID — live in the `tend`
environment, whose policy names the default branch and any
`protected_branches`. Every generated job that reads a secret carries
`environment: {name: tend, deployment: false}`; jobs that hold none
(tend-mention-relay, below) must not, since naming it would cost them the refs
the policy excludes. `deployment: false` keeps GitHub from filing a
deployment record for a job that deploys nothing — under
`pull_request_target` those land on the pull request itself, one line per
push — and leaves the policy check untouched. This closes
the classic no-merge exfiltration: a write-scoped actor (a leaked PAT, or a
hijacked session that can push a branch) commits a workflow that prints the
secrets and reads them from its own run. Branch protection never touched
that path — it bounds what gets *merged*, not what a run can *read* — but
the environment does: the pushed workflow's run carries the branch's own
ref, and the job naming the environment fails with zero steps executed
(observed on a live probe).

Where each trigger runs. A ✓ row was observed on a live probe; the rest
carry the ref of the family they belong to and were not probed
individually. GitHub's "runs in the context of the default branch"
sentence for review events refers to which workflow *file* runs, not to
`GITHUB_REF` — which is why the review rows had to be measured rather than
read off the docs:

| Trigger | `GITHUB_REF` | `tend` gate | |
|---|---|---|---|
| `issue_comment`, `repository_dispatch` | default branch | passes | ✓ |
| `issues`, `schedule`, `workflow_run` | default branch | passes | |
| `pull_request_target` | PR base branch | passes for admitted bases | ✓ |
| `push` to a feature branch | that branch | refused | ✓ |
| same-repo `pull_request` | `refs/pull/N/merge` | refused | |
| `pull_request_review`, `pull_request_review_comment` | `refs/pull/N/merge` | refused | ✓ |

The `pull_request_target` refusal on other bases is itself load-bearing:
that event runs the *base* ref's workflow file, so a PR targeting a
bot-pushable branch would execute whatever tend-review.yaml that branch
carries, with the secrets. The cost is that a stacked PR — one based on
another PR's branch — gets no review until it retargets an admitted
branch; the refused run fails visibly rather than skipping. (The probe
observed the default-base case; the base-ref value is GitHub's documented
`GITHUB_REF` for the event, and the refusal is the mechanism the `push`
row measures.)

Only one workflow legitimately needs a refused ref: tend-mention answers
review submissions and inline review comments. The merge ref can
never be admitted, because a same-repo `pull_request` run executes the PR
head's own workflow files on that same ref — admitting it would hand a
pushed workflow the secrets back. So those events go to a separate
workflow, tend-mention-relay, whose one secretless job (only the
workflow-scoped `GITHUB_TOKEN`, `contents: write`, which is what the
dispatch POST requires — `read` is refused 403, probed) receives the
review event and re-posts it as a `repository_dispatch` carrying
identifiers only (`{kind, pr, id}`). The dispatch run, in tend-mention,
carries the default branch, passes the gate, and its verify job re-reads
the review or comment from the API before applying the engagement
checks. Any write-scoped actor can forge such a dispatch, which
is why the payload carries no judgement: a forged dispatch runs the same
reviewed workflow file, faces the same engagement checks against the record
GitHub holds, and can point the bot at nothing the actor couldn't reach by
posting a comment.

The relay stops at the repository boundary. A review event on a *fork* PR
does start a run, on the merge ref and from the PR head's own workflow
files, but that run's token is read-only whatever the file asks for: probed
with `contents: write` declared, its `POST /repos/…/dispatches` still
returns 403, where the same request from a same-repo run with the same
permission succeeds. So a fork PR's reviews reach the bot through the
notifications poll, minutes later rather than seconds. The daily live scan is
the slower backstop for a missed notification. That is the cost of the
property the relay depends on: a fork run that could start a secret-bearing
run in the base repo would be a fork run with write access to it.

*Release secrets* (registry tokens, signing keys) stay outside yolo's
default-branch risk only when their environments admit tags or other
bot-inaccessible refs, not the default branch. A tag-target ruleset gates
`creation` and `update` with
admin-only bypass; `update` is what force-push of an existing tag fires, so
it must be blocked alongside `creation`. Rulesets are the only mechanism —
GitHub sunset tag protection rules in 2024 — and the `creation` rule also
refuses `POST /repos/{repo}/releases` when the named tag does not exist
yet, so the Releases API is not a way around it. The
`credential-environments` sweep above verifies every such environment.

Immutable releases close the separate write path: once a release is published,
GitHub locks its assets and associated tag. This is a repository setting, not a
ruleset inference; `tend check` verifies it directly and `--fix` enables it.
Both the read and the write take repository admin, so the nightly run — which
holds only the bot's write-scoped token — verifies the newest published
release's own `immutable` flag instead, and fails when that release can still
be rewritten. The setting is prospective, so enable it before the repository's
next release.
It does not make `release: published` safe for secrets: a write actor
can still publish a new release against an existing unpublished tag.

The gate bounds what a run can *read*; it does not by itself bound *when*
a workflow fires. In maintainer mode, a workflow reachable only by updating
a gated ref (`push: tags:` for release, `push: branches: [main]` for continuous
deploy) needs an admin action and runs code fixed by that ref. In yolo, a
default-branch push follows a bot merge and may run with generic credentials;
tag pushes still need an admin action. `schedule`, `workflow_run`,
`deployment` and an input-free `workflow_dispatch` cannot give the bot
control of a run's payload beyond the code on its ref.

Three triggers let the bot supply a run's payload without landing code through
the merge gate, even when the ref policy admits only protected refs:
`release: published` (creating a release against an existing tag takes no
tag operation, and the release's body and assets are the bot's own),
`repository_dispatch` (`client_payload` wholesale), and a
`workflow_dispatch` carrying inputs. A ref policy admitting only
bot-inaccessible refs cannot gate these; only a required reviewer can. Even
when a yolo policy admits the default branch, a fixed workflow can pass the
event payload to a credential-bearing step without running bot-merged code.
Such a workflow puts its secrets in an environment behind a required reviewer.
The sweep verifies any such environment,
keyed on the credential rather than the name, with the bot excluded from
the reviewer list since a bot that can approve its own run makes the wait
a formality.

An OIDC publish or deploy (PyPI or npm trusted publishing, a cloud role)
stores no secret, so the environment is the whole gate on GitHub's side —
the token's `sub` names it, and a relying party can require that claim. A
job holding `id-token: write` outside any environment has no gate at all:
the token carries no environment claim, and the bot can mint it from a
branch it pushes, which any trust policy pinning the repository but not the
ref accepts. The sweep covers both cases: an environment a job mints OIDC
in holds a credential even with nothing stored in it, and a job minting one
outside any environment is reported on its own. Tend's own generated
workflows request no `id-token`.

*Migration.* Environment secrets overlay repo-level ones, and a job naming
an environment that does not yet exist auto-creates it with no policy and
runs normally (observed on a live probe). A repo that has not completed the
migration therefore keeps working on its repo-level secrets, with exactly
its old exposure — the gate protects nothing until the policy is set, the
secrets are moved, and the repo-level copies are deleted. `tend check`
fails on each missing piece until then: it verifies the environment exists,
its policy is a named list matching exactly the branches that same run
confirmed for Tend's runtime, the operational secrets are present in it, and no
repo-level copy remains. Confirmed, not configured: a branch named in
`protected_branches` that does not exist yet cannot be admitted, or the
policy would claim safety for a ref whose live protection cannot be checked.
The canonical ruleset still blocks its future creation, update, and deletion.
`tend check --fix` creates the environment and reconciles its policy; moving
the secrets stays manual, since their values cannot be read back.

The policy must be that named list rather than GitHub's "protected
branches" mode, which keys on whether *a* rule covers the branch and not on
who may push it. Probed: with that mode selected, a branch protected only
by `required_linear_history` — which blocks no push — took a plain push and
then read an environment secret, while an unprotected branch was refused
with zero steps.

Both environment chains assume the bot remains at write permission; an admin
bot voids their ruleset and reviewer boundaries. Configuration recipe:
`plugins/install-tend/skills/install-tend/references/security-model.md`.

Everything else in this section is defense in depth: useful, but not
load-bearing.

### Agent execution boundary

The harness has three states and three transitions:

| State | The job's tree on disk | The agent's view of it | Sandbox processes | Allowed next step |
|---|---|---|---|---|
| **trusted setup** | runner-owned, reviewed base, whatever `setup:` built | none | none | launch |
| **running** | unchanged, and unreachable except through the view | writable copy-on-write overlay at the same paths | one systemd unit | reap |
| **quiescent** | unchanged, byte for byte | unmounted | none | bounded export |

Each transition is a bottleneck with one job:

- **The view** puts the agent in the job's own checkout and home, at their real
  paths, with the job's PATH and environment. The supervisor
  (`shared/steps/launch_agent.py`) mounts an overlay of the runner's home at a
  staging path in the per-run `/var/tmp` runtime container, and the agent's
  unit binds it over the home. The lower layer is a read-only bind of that
  home, *idmapped* so the runner's and the sandbox's ids swap; the upper layer
  sits beside it in the runtime container. The agent reads what `setup:` left
  and writes wherever the runner could — a path only root can write is not one
  of them. Every write lands in the upper layer, so the runner's filesystem
  stays byte-for-byte what `setup:` left and no cleanup step can fail open. The
  supervisor unmounts the view after the reap. Because the overlay is of the
  home, the job's checkout must sit inside it; the sandbox setup step refuses a
  self-hosted work folder elsewhere by name rather than letting the agent start
  on a checkout it cannot write.

  The idmap makes the agent the runner account for file permissions on that
  tree. **Anything `setup:` leaves readable in the runner's home or checkout is
  readable by the agent, and so by anyone who can open a pull request.**
  `docs/tend.example.yaml` tells consumers to log in only in steps tend does
  not run. Two things in the home are unreadable to the agent (the unit's
  `InaccessiblePaths=`), both derived from the running
  job: the Actions runner's installation, found from the `Runner.Worker`
  ancestor (every entry except one a job path lives under, since the default
  self-hosted layout keeps `_work` there), and GitHub's file-command
  directory, which holds `$GITHUB_OUTPUT` and `$GITHUB_STATE` values no step
  exported. A `$GITHUB_ENV` export is an environment variable by launch and
  crosses. Tend's own runner-side secrets (the proxy CA key, the Codex
  credentials) sit in a 0700 directory in the runtime container, outside the
  home. The hosted integration test asserts they are unreadable from inside,
  and sweeps the home for a runner credential the mask missed. The auto-memory
  key is read after that container is deleted, so it sits beside the memory
  directory in `/var/tmp` instead, a 0600 runner file.

  Root runs `mount`, `umount`, `chown`, `systemd-run`, `systemctl` and `pkill`
  from fixed argvs, and no file of Tend's. The job's environment crosses in a
  0600 file in the private directory, which the unit reads as its
  `EnvironmentFile=` and the supervisor removes after the run, rather than on
  the command line, where `sudo` would log it. It needs kernel 5.19,
  util-linux 2.39 and systemd 247, with no fallback.
- **Content ingress** happens inside the sandbox. The workflow's checkout
  arrives on reviewed code, and the lifecycle's first step selects the event's
  topology in it — the PR's merge or head ref, a mentioned PR's head branch, or
  the base branch — then pins startup configuration to the chosen base commit.
  Git parses a contributor's packfile as the sandbox uid, and the fetch
  authenticates through the credential proxy. The Codex binaries and the
  immutable agent environment live in one runner-owned, sandbox-readable
  runtime directory under `/var/tmp`; the sandbox cannot write it. The unit
  mounts private tmpfs over `/tmp` and `/dev/shm`, so tooling that hard-codes
  a path there writes to a directory that dies with the unit.
- **Launch and lifetime** runs the event checkout and the whole Claude or
  Codex turn as one transient systemd unit. Its settings are the boundary, and
  systemd enforces each of them:
  - the filesystem read-only (`ProtectSystem=strict`) except the view, the
    sandbox's home and the auto-memory directory, and a minimal `/dev`;
  - the sandbox uid, with no capabilities, no new privileges and no new
    namespaces (`RestrictNamespaces=`);
  - other users' processes hidden from `/proc` (`ProtectProc=invisible`). There
    is no PID namespace, which systemd adds only in version 257; the separate
    uid means the agent can signal nothing outside its own processes;
  - no `AF_UNIX` socket and no io_uring. The network namespace does not cover
    sockets on the filesystem (the system bus, systemd-resolved's DNS), so the
    family is refused outright, and io_uring could open one without the
    `socket(2)` call the filter sees.

  The lifecycle probes AF_UNIX denial and the view (writable, masks
  unreadable) before anything from the event runs. The hosted integration test
  additionally asserts that direct host loopback and DNS are unreachable, that
  the credential proxy is reachable, and that nothing of either unit or the
  view outlives the run. The unit is built from the runner image's systemd,
  which moves with the image rather than with a Tend commit; `test-sandbox`
  runs it every six hours.
- **Authority brokerage** leaves long-lived credentials in runner-owned
  proxies. The unit's network namespace holds loopback alone. A socket unit
  listens on the credential proxy's port inside it, and `systemd-socket-proxyd`,
  outside it, forwards each connection to the proxy; the agent gets dummy
  credentials. Codex and Claude do not add a second nested filesystem sandbox.
- **Result export** begins only after the unit exits and the sandbox UID has
  no live process. The supervisor reads fixed result and skill-summary files,
  and the later token step copies bounded session data, all through no-follow
  reads; the agent never receives GitHub's command-file paths. Codex then stops
  its runner-owned Responses proxy, and one fixed cleanup deletes the per-run
  runtime directory. Subsequent steps consume the bounded exports or the
  sandbox user's session tree; none executes from the deleted runtime.

Local `setup:` composites and all their POST chains therefore see the same
reviewed tree they started on, with no post-agent restore.

**Action distribution integrity.** Generated workflows pin the composite
action to the generator's own release version
(`max-sixty/tend/<harness>@X.Y.Z`), never a floating ref. Release-tag
immutability is the boundary this relies on for new releases: GitHub's
immutable-releases setting locks each release's assets and its tag when it
is published. The tag ruleset also restricts updates. Tend's releases from
before the setting was enabled have no uploaded assets and their tag code is
protected by a no-bypass tag ruleset, but their GitHub release records are not
retroactively immutable. The separate all-tags ruleset prevents the bot from
creating or repointing any release tag, so a leaked bot token or hijacked
session cannot change the action code every consumer already runs. Consumers
extend trust to `max-sixty/tend`'s release-tag integrity the same way they
trust any third-party action's publisher; pinning to `X.Y.Z` (or a commit
SHA) bounds that trust to a reviewed, immutable point.

**Config pinning.** Before the agent starts, both harnesses restore every
`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `AGENTS.override.md`, `.claude/`,
and `.agents/` at any depth from the PR base branch. Their CLIs load nearby
instruction files and skills from those directories. Both harnesses also
restore RCE-relevant config at the root: `.mcp.json`, `.claude.json`, `.gitmodules`, `.ripgreprc`, and
`.husky`. A malicious PR's `SessionStart` hook, MCP server, or injected skill
is reverted before an agent reads it. The restoration is
`git restore --source=<exact base commit>` in shell:
base-branch versions are written back, fork-added paths removed, and a
fork-planted symlink replaced rather than written through. The root path list
and ordering mirror claude-code-action's `restore-config.ts`. The PR's own
versions stay readable at `git show HEAD:<path>` for a review that wants to see
what it changed.

**Setup runs on reviewed code.** Consumer `setup:` steps execute as the runner
user against the stable Actions checkout: the default branch, or in
`tend-review` the PR's reviewed base. The PR's own tree reaches that checkout
only inside the sandbox, so a contributor's build backend and dependencies
execute only there, when the agent builds or tests that tree, as the non-sudo
sandbox user in the same unit.

Yolo refuses all runner-side `setup:` because ordinary code merged by the bot
could steer even a fixed command running against the default branch. It also
refuses workflow and job overrides so credential-bearing jobs retain their
audited shape.

After the unit exits, the trusted supervisor kills and verifies the complete
sandbox UID process tree, then copies only size-bounded fixed outputs. The next fixed
action step deletes the per-run `/var/tmp/tend-runtime.*` directory, which
holds the staged lifecycle bundle, Tend's runner-side secrets and the view's
upper layer. On a self-hosted runner nothing deletes the `tend-sandbox` user,
so `/home/tend-sandbox` persists between jobs under one shared uid, and what
one run leaves there the next run's agent can read. A run whose reap failed
deletes nothing: the live writer is the reason not to delete underneath it.

**Credential isolation.** Both harness actions run the agent as a separate
non-sudo `tend-sandbox` user, sharing the GitHub proxy machinery under the
top-level `proxy/`. The bot PAT lives only in a local mitmproxy that the agent
reaches over `HTTPS_PROXY`; the proxy injects it only for exact GitHub hosts
and tunnels everything else. This authenticates API and git operations for any
repository the bot account can access; credential isolation protects the PAT
itself rather than restricting those operations to the triggering repository.
Claude's Anthropic credential (OAuth token or API key) uses the same proxy and
is injected only for `api.anthropic.com`. Under API auth, Codex's OpenAI key is
instead read from stdin by OpenAI's hardened Responses API proxy. It forwards
only `POST /v1/responses` upstream and answers `GET /shutdown` on loopback so
Tend can stop it during teardown. The agent holds only a dummy PAT and the
local inference endpoint. Under subscription auth, it additionally receives an
expiring access-only `auth.json`, but not the rotating refresh token. A
different UID with no sudo cannot read either proxy's
`/proc/<pid>/environ`; the persisted checkout credential is stripped before
launch; and the PAT and API credentials are never
written to the agent's env or disk. The injection
allowlist is exact-match on the connection's real destination, so a request to
a lookalike host gets no token. The GitHub proxy is launched by a pinned `uv`
that Tend installs into its own directory, off `$PATH`, so the process holding the PAT
starts from a known binary rather than whatever a consumer's
`setup:` happened to leave on the runner. (`claude` is Node and ignores the
system trust store, so it trusts the proxy CA via `NODE_EXTRA_CA_CERTS`.) The
job's PATH crosses entry for entry, with the sandbox home's `bin` prepended and
a pinned `uv` fallback appended.

**Session-log upload.** The token-usage step uploads the agent's session JSONL
only after the unit and sandbox UID are quiescent. One privileged
helper opens every source component and descendant relative to no-follow file
descriptors, copies regular files only, and enforces per-file, total-byte, and
file-count bounds. Symlinks, devices, and FIFOs never enter the runner-owned
artifact tree.

When a repo owns subscription renewal, its weekly refresh job checks out no
consumer code and gives Codex only Tend's fixed refresh prompt. Codex receives
the full refresh bundle there; the environment-write PAT appears only in the
separate publish step after Codex exits. Each repo-owned job needs a unique
full refresh-token chain. A Mac rotator can temporarily publish access-only
`CODEX_AUTH_JSON` to several repos whose generated refresh workflows are
disabled, but those repos cannot authenticate after token expiry if the Mac is
offline.

**Rate limiting.** Burst detection (10 PRs or issues per 20 minutes) and
spike detection (today's volume vs 6-day baseline, scaled per repo) abort
the run before the agent starts, catching runaway loops between workflows.
The check runs as its own step in the composite action, so a
prompt-injection attack inside the agent session cannot skip it. Concrete limits live in
`shared/steps/rate_limit_preflight.py`.

The spike limit is resumable by a maintainer, the burst limit is not. On a
spike trip the run files or reopens a `tend-rate-limit` issue listing the
runs it refused; closing that issue doubles the ceiling for the rest of the
UTC day, and each further close doubles it again, so the limit re-arms
after use rather than switching off. Approval is a check rather than an
instruction: the preflight counts only closes whose actor is not the bot,
and since GitHub admits only the author or a triage/write collaborator to
close an issue — and the bot is the author — that leaves exactly the
maintainers. The bot cannot approve itself even if a prompt injection tells
it to, and there is no allowlist to maintain.

Refused runs do not retry on their own; the issue's table carries their
links. Automating that is deferred (see `TODO.md`).

**Fixed prompts and marketplace skills.** The prompt and skill set come from
the composite action and the tend marketplace, not from the PR. An attacker
can influence what the agent *reads* (the diff, the issue body) but not the
*instructions* it follows or the *tools* it has access to.

The account the session signs in as is the other source that could add to that
set without review. Claude Code syncs the skills and plugins enabled on the
signed-in claude.ai account into a session, and separately auto-fetches that
account's MCP cloud connectors, headless runs included — so anything enabled on
the bot's account would become an instruction and tool source for every
consumer's CI session, in a process that pushes commits and posts as the bot,
reviewed by nobody in either repository. The session settings
`shared/steps/run_claude.py` writes refuse all three: `syncClaudeAiSkills` and
`syncClaudeAiPlugins` false, `disableClaudeAiConnectors` true. Its test asserts
that settings file exactly, so a key that silently stops being written fails
the suite rather than quietly reopening the surface. The sync pair is honored
only as `false`, and from `.claude/settings.local.json` or `--settings` rather
than project `settings.json` — the layer the action already writes. The feature
it refuses turns on server-side per account, so the refusal has to be in place
ahead of it rather than written in response to it.

**GitHub's log masking.** Secrets stored in GitHub are automatically redacted
from workflow logs. This is exact-match only — if a token appears
base64-encoded or embedded in JSON, the redaction misses it.

## Remaining risks

**The agent executes attacker-controlled code.** This remains expected behavior.
When an agent runs tests or build commands on a fork PR, it executes code the
attacker wrote. A `Makefile`, `package.json` postinstall hook, or
`conftest.py` can do anything the sandbox user can and send data over the
network. It cannot read the PAT or API credentials; subscription mode's
expiring access token is the deliberate exception described below. Config
pinning prevents
*Claude Code's own* startup hooks from being hijacked, but it can't prevent
an agent from voluntarily running `make test` on a repo where `make test` has
been weaponized. The agent's systemd unit contains that process tree to the
view of the job's home, the sandbox home, scratch paths, and brokered network.
This protects the runner's own filesystem and host authority; it does not make
the checked out repository content confidential or prevent the agent from
deliberately publishing content it can read.

**Write access still starts workflows.** With the operational secrets
environment-gated, a write-scoped actor can no longer read them out of a
workflow it pushes; what it keeps is invocation. It can post the comments
and reviews that wake the bot, and it can forge the `repository_dispatch`
that tend-mention's relay uses — both start only the default branch's
reviewed workflow files, with the engagement checks applied to the record
GitHub holds rather than to the payload.

**Data exfiltration.** An attacker who gets code execution can send
repository contents and anything else the agent can read to a server of their
choosing, or publish it through what the run leaves behind: the workflow log,
the job summary, the uploaded session logs, or a post to GitHub as the bot.
The unit's network namespace removes direct network access, DNS included, but
Tend's credential proxy connects to any host the agent names, the runner's
loopback included, because the agent needs general package and GitHub access;
destination allowlisting is deferred.
Credential isolation keeps the PAT and API
credentials out of what a hijacked session can send. A Codex subscription
session can send its expiring access token, but it never receives the rotating
refresh token or the PAT that rewrites environment secrets.

**Credential theft.** Isolation minimizes the chance that a hijacked session
can steal the long-lived tokens, but it does not protect against compromise of
the runner-owned proxy or the runner itself. A stolen classic PAT remains valid
until revoked and grants access to every repository both its scope and the bot
account can reach. A stolen subscription access token remains valid until it
expires. Under maintainer mode, the merge restriction prevents that credential
from landing code. Under yolo, it can land ordinary code by design, while the
control-plane rule, environment gates, and immutable releases still prevent
repository takeover and release rewriting unless one of those runner-owned
boundaries is separately compromised.

**Prompt injection without code execution.** Even without hijacking the
tools, an attacker who controls what Claude reads can influence its behavior.
A carefully crafted PR description or issue body could get Claude to approve a
bad PR, post misleading comments, or dismiss legitimate review concerns. Fixed
prompts and skill instructions reduce this risk but can't eliminate it —
Claude ultimately reasons about attacker-controlled text.

**Persistent auto memory.** The experimental `memory_gist: true` setting lets
Claude carry model-authored notes into unrelated later runs. Those notes are
context, not policy, and may preserve stale facts or the effect of an earlier
prompt injection. The adapter accepts only a bot-owned secret Gist bound to the
exact repository, signs its per-run baseline, rejects symlinks and nested paths,
and skips the entire save when it observes a conflict. A secret Gist is readable
to anyone who learns its URL, so the Gist ID stays out of committed public files
and the experiment is refused for private repositories. It is not hidden from
the session: the agent's proxied bot access can list the account's Gists.

Deferred hardening options (Haiku pre-screening, read-only fork PRs, and
outbound destination allowlisting) live in `TODO.md`.
