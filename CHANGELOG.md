# Changelog

Notable changes per release. The `## X.Y.Z` section for each version is
published verbatim as that version's GitHub Release notes
(`.github/workflows/pypi-release.yaml`). Newest first. Releases before
0.1.1 predate this changelog; see the compare views at
https://github.com/max-sixty/tend/compare for their history.

## 0.1.7

### Improved

- **Generated workflows pin `actions/checkout` to v7.** All generated workflows (and tend's own) move from checkout v6 to v7. The review workflow opts into v7's fork-PR checkout guard (`allow-unsafe-pr-checkout: true`), which otherwise refuses to check out a fork's `refs/pull/N/{merge,head}` under `pull_request_target` (the "pwn request" guard), so fork-PR reviews keep running. ([#725](https://github.com/max-sixty/tend/pull/725))
- **Both Claude harnesses update to claude-code 2.1.185.** ([#719](https://github.com/max-sixty/tend/pull/719))
- **The bot surfaces a blocking scope rule instead of silently routing around it.** When a `running-in-ci` scope restriction blocks the right action — e.g. engaging an existing upstream thread in another repo — the bot now surfaces the blocker on the triggering thread and offers either to take the upstream action on approval or to relax the rule via the consuming repo's `running-tend` overlay, rather than substituting a second-best local workaround without signaling it hit a wall. ([#717](https://github.com/max-sixty/tend/pull/717))

### Fixed

- **CI-poll loops fit the Bash tool's 10-minute cap.** The bundled `running-in-ci` poll recipes cap their `sleep` loops at 9 iterations and call the Bash step with `timeout: 600000`, so the harness no longer auto-backgrounds a longer loop and strands the gated follow-up (dismissing a stale approval, posting failure analysis). ([#695](https://github.com/max-sixty/tend/pull/695))
- **Nightly workflow-regen bases its worktree on an open PR, not branch-ref existence.** The `nightly` skill's regen step now bases on the `tend/update-workflows` branch only when an open PR rides it, and otherwise bases on `HEAD` and drops any leftover remote branch. A PR previously closed without merge no longer leaves a stale branch that inflates the diff, produces an inaccurate PR body, or defeats the no-value skip. ([#721](https://github.com/max-sixty/tend/pull/721))

### Documentation

- The codex `effort` value list in the README and the install-tend skill is corrected to `low | medium | high | xhigh`. ([#710](https://github.com/max-sixty/tend/pull/710))

### Internal

- Composite-action step bodies are de-duplicated into scripts under `shared/steps/`, and each harness action lives under a harness-named path. Generated workflows now invoke `max-sixty/tend/claude@X.Y.Z` (and `claude-interactive`) rather than the bare-root default; existing pinned refs keep resolving and the nightly regen stamps the new path automatically. ([#712](https://github.com/max-sixty/tend/pull/712))
- `review-reviewers` documents the `pull_request_review` self-trigger as expected (non-)behavior, and the `worker-deploy` comment corrects the live-stream count to two. ([#707](https://github.com/max-sixty/tend/pull/707), [#711](https://github.com/max-sixty/tend/pull/711))

## 0.1.6

### Improved

- **The default `claude` harness runs the official binary headless behind the credential proxy.** The root `action.yaml` was rewritten to run `claude -p` as a non-sudo `tend-sandbox` user behind the same credential-injecting mitmproxy the interactive harness uses, replacing the `anthropics/claude-code-action@v1` wrapper that handed the bot PAT and the Anthropic credential to the agent directly. Both credentials now live only in the proxy and never enter the agent's environment or disk; completion is the `claude -p` exit code. The action gains `claude_version`, `timeout_seconds`, and `mitmproxy_version` inputs and drops the unused claude-code-action passthroughs. ([#704](https://github.com/max-sixty/tend/pull/704))

### Internal

- Bundled skills replace guidance duplicated from `running-in-ci` — triage's recheck-before-posting and review-runs' read-only-mount workaround — with references to the canonical sections. ([#703](https://github.com/max-sixty/tend/pull/703))
- The `claude-smoke` workflow that exercises the headless harness end-to-end becomes `workflow_dispatch`-only, matching `interactive-smoke`. ([#706](https://github.com/max-sixty/tend/pull/706))

## 0.1.5

### Improved

- **Interactive harness isolates both credentials behind the proxy.** Phase 2 extends the credential-injecting proxy to the Anthropic model credential, so the sandboxed agent holds only dummies for both the GitHub PAT and the model token while the runner-owned proxy injects the real values per host. The agent toolchain now installs directly as the non-sudo sandbox user (dropping a ~200 MB per-run copy), and the proxy also injects the PAT for `raw.githubusercontent.com`. ([#686](https://github.com/max-sixty/tend/pull/686), [#683](https://github.com/max-sixty/tend/pull/683), [#684](https://github.com/max-sixty/tend/pull/684))
- **install-tend isolates each bot's auth in a per-bot `GH_CONFIG_DIR`.** Bot credentials live in a dedicated `~/.config/gh-bots/<bot-name>` dir selected per command and stored outside the OS keychain, removing the `gh auth switch` choreography that could strand a bot as the active account and 403 a maintainer's pushes. ([#688](https://github.com/max-sixty/tend/pull/688))
- **Interactive harness updated to claude-code 2.1.179.** The pinned `claude` binary resolves `--model opus` to Opus 4.8. ([#697](https://github.com/max-sixty/tend/pull/697))

### Internal

- Bundled skill refinements: nightly skips stamp-only workflow-regen PRs and scopes "Notable changes" to adopter-relevant entries, review-reviewers keeps an audit trail on empty-window cycles, and over-prescriptive guidance is reframed as examples and open frames. ([#693](https://github.com/max-sixty/tend/pull/693), [#692](https://github.com/max-sixty/tend/pull/692), [#689](https://github.com/max-sixty/tend/pull/689), [#690](https://github.com/max-sixty/tend/pull/690), [#675](https://github.com/max-sixty/tend/pull/675))
- tend-repo maintenance: a weekly task keeps the pinned agent binaries current, integration-fixture secrets reseed outside the sandbox, and the secret env-gating rejection analysis is recorded alongside a CLAUDE.md restructure. ([#696](https://github.com/max-sixty/tend/pull/696), [#685](https://github.com/max-sixty/tend/pull/685), [#687](https://github.com/max-sixty/tend/pull/687))

## 0.1.4

### Improved

- **Claude harnesses run with `bypassPermissions`.** The previous `dontAsk` mode hard-denies writes to Claude Code's protected paths (`.github/`, dotfiles), blocking autonomous fixes that touch those files. Everything the bot writes still lands through a reviewed PR. ([#677](https://github.com/max-sixty/tend/pull/677))
- **GitHub Releases publish on tag push.** The release workflow extracts the version's section from `CHANGELOG.md` and creates the release; 0.1.1–0.1.3 are backfilled. Nightly workflow-update PRs now summarize notable upstream changes instead of pasting a file list. ([#678](https://github.com/max-sixty/tend/pull/678))
- **install-tend triages an existing bot PAT before minting a new one.** The bot-token step runs the scope audit and routes to reuse, scope refresh, or first-time login. ([#680](https://github.com/max-sixty/tend/pull/680))
- The review skill checks the PR's check rollup before approving, so visible CI failures aren't rubber-stamped. ([#667](https://github.com/max-sixty/tend/pull/667))

### Fixed

- The interactive harness passes GitHub Actions context env vars (`GITHUB_RUN_ID`, `GITHUB_REPOSITORY`, …) into the sandbox; skill recipes for run self-reference and URL construction depend on them. ([#664](https://github.com/max-sixty/tend/pull/664))

### Documentation

- README clarifies that the weekly workflow approves dependency PRs rather than auto-merging them. ([#673](https://github.com/max-sixty/tend/pull/673))

### Internal

- Skill refinements across running-in-ci, triage, and ci-fix: end the turn only when work is shipped, defer test suites to PR CI, split CI monitoring into gated/ungated cases, label transient-tracker issues `tend-outage`, and carve out bot-authored machine-report issues. ([#661](https://github.com/max-sixty/tend/pull/661), [#671](https://github.com/max-sixty/tend/pull/671), [#669](https://github.com/max-sixty/tend/pull/669), [#670](https://github.com/max-sixty/tend/pull/670), [#666](https://github.com/max-sixty/tend/pull/666))

## 0.1.3

### Improved

- **Interactive harness isolates the GitHub PAT.** The agent runs as a non-sudo `tend-sandbox` user; the PAT lives only in a local credential-injecting proxy that adds it for GitHub hosts, so it never enters the agent's environment. ([#652](https://github.com/max-sixty/tend/pull/652))
- **Prior-run context recall.** The bot recalls context from earlier runs on the same issue or PR instead of starting cold each invocation. ([#649](https://github.com/max-sixty/tend/pull/649))
- tend now dogfoods the `claude-interactive` harness for its own review/mention/triage/ci-fix workflows. ([#622](https://github.com/max-sixty/tend/pull/622))

### Fixed

- **Worker reliability:** throw on Search failures instead of caching an empty payload, return 503 (not a 200 all-zero payload) on cold-cache failure, and share the `/activity` payload across colos via a KV tier. ([#648](https://github.com/max-sixty/tend/pull/648), [#650](https://github.com/max-sixty/tend/pull/650), [#653](https://github.com/max-sixty/tend/pull/653))
- Site `liveData` polling self-schedules so ticks can't overlap. ([#655](https://github.com/max-sixty/tend/pull/655))
- Mention workflow skips no-op sessions for undirected bot comments. ([#608](https://github.com/max-sixty/tend/pull/608))
- `review-reviewers` pre-creates the monthly tracking issue to eliminate a matrix race. ([#657](https://github.com/max-sixty/tend/pull/657))

### Internal

- Skill refinements across running-in-ci, notifications, review-runs, ci-fix, and running-tend; dead-input and template cleanups in the actions and generator.

## 0.1.2

### Improved

- **`claude-interactive` harness.** A PTY-supervised alternative to the Agent SDK: runs the official `claude` binary under a `script(1)` supervisor with a Stop-hook sentinel. ([#609](https://github.com/max-sixty/tend/pull/609))
- **Per-workflow harness override.** Trial a different harness (and matching model) on one workflow at a time. ([#612](https://github.com/max-sixty/tend/pull/612))

### Fixed

- Mention workflow uses `comment.updated_at` so edit events report accurate queue delay. ([#595](https://github.com/max-sixty/tend/pull/595))
- Interactive harness: token-usage jq parser double-iterator fix, and the `-newer` filter dropped from the session-JSONL parser. ([#616](https://github.com/max-sixty/tend/pull/616), [#614](https://github.com/max-sixty/tend/pull/614))

### Internal

- Skill hardening: close the env-filter loophole for `ALL_INPUTS` secrets, recheck PR state before pushing follow-up commits, raise the bar for repo-overlay PRs, and trigger upstream-bot rebases instead of manual conflict resolution. ([#599](https://github.com/max-sixty/tend/pull/599), [#573](https://github.com/max-sixty/tend/pull/573), [#604](https://github.com/max-sixty/tend/pull/604), [#605](https://github.com/max-sixty/tend/pull/605))

## 0.1.1

### Internal

- Skill refinements: a weekly integration-test recipe and a release-workflow fix. ([#590](https://github.com/max-sixty/tend/pull/590), [#589](https://github.com/max-sixty/tend/pull/589))
