<!-- tend:draft-review -->
This is feedback on the draft, not a merge verdict. Marking the PR ready requests the full review.

The tracker-matching gaps from the last round are closed: 4a's occurrence count, 4b's dedup and the `Fixes #<n>` lookup all compare evidence per **Claims of recurrence** now.

The new eval is in `plugins/tend-ci-runner/evals/`, which means it ships. `shared/steps/install-tend-plugins.sh` copies the whole `plugins` tree into every consumer's sandbox (`cp -a "$MARKETPLACE_SRC/.claude-plugin" "$MARKETPLACE_SRC/plugins" ...`), so Leaf's PR discussion and CI log would be installed in every repo running tend, and none of them run the eval. The repo already keeps evals out of the plugin: cases go in `evals/cases/<name>/`, and `evals/prepare.py` stages them into a throwaway plugin copy under `.tmp/evals/prepared/`. As a result, `evals/AGENTS.md` and this README now give different run recipes and different places for cases.

This case won't fit `prepare.py` without changes. Its `main()` reads `source["sha256"]` and cuts a transcript, while this reconstruction uses fixture files through `context.add_dirs`. One option is to teach `prepare.py` to stage a fixture-only case. The other is to keep the case under `evals/` with its own staging step. Either way, it stays out of what consumers install, and `evals/AGENTS.md` remains the single description of how cases are added and run.
