# Changelog

Notable changes per release. The `## X.Y.Z` section for each version is
published verbatim as that version's GitHub Release notes
(`.github/workflows/pypi-release.yaml`). Newest first. Releases before
0.1.1 predate this changelog; see the compare views at
https://github.com/max-sixty/tend/compare for their history.

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
