# `max-sixty/tend/claude`

The Claude SDK harness, exposed at the `/claude` subpath so all three harnesses
share a named-subpath ref (`max-sixty/tend/claude`, `max-sixty/tend/interactive`,
`max-sixty/tend/codex`). This is the ref the generator emits for `harness: claude`.

The action is defined once, at the repo-root `action.yaml`. The entries here are
symlinks to it and to the resources its steps read (`shared/`, `.claude-plugin/`,
`plugins/`), so `${{ github.action_path }}`-relative lookups resolve whether the
action runs as `max-sixty/tend/claude@X.Y.Z` (from this directory) or
`max-sixty/tend@X.Y.Z` (from the root, retained for already-pinned adopters).

Why symlinks: a composite action can't delegate to a sibling local action with a
relative `uses:`. That path resolves against the consumer's `$GITHUB_WORKSPACE`,
not the action's own directory (actions/runner#1348). Symlinks are the
zero-duplication alias.
