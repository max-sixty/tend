# `max-sixty/tend/claude`

The Claude SDK harness, exposed at the `/claude` subpath so all three harnesses
share a named-subpath ref (`max-sixty/tend/claude`, `max-sixty/tend/interactive`,
`max-sixty/tend/codex`). This is the ref the generator emits for `harness: claude`.

The action is defined once, at the repo-root `action.yaml`. The entries here are
symlinks to it and to the resources its steps reach through `github.action_path`:

- `action.yaml` — the canonical action
- `shared` — the system-prompt base, read in place
- `interactive` — the credential proxy under `interactive/proxy/`, which both
  Claude harnesses run
- `.claude-plugin` — the marketplace manifest

When the action runs as `max-sixty/tend/claude@X.Y.Z` (`github.action_path` is
`.../claude`), every `github.action_path` lookup resolves through a symlink back
to the real repo-root resource, exactly as it does at `max-sixty/tend@X.Y.Z` (the
root, retained for already-pinned adopters). The sandbox marketplace step
resolves `github.action_path/.claude-plugin/..` so its `cp -a` copies the real
`.claude-plugin` and `plugins` dirs (preserving their internal symlinks), not the
alias's own symlinks.

Why symlinks: a composite action can't delegate to a sibling local action with a
relative `uses:`. That path resolves against the consumer's `$GITHUB_WORKSPACE`,
not the action's own directory (actions/runner#1348). Symlinks are the
zero-duplication alias.
