# CI failure attribution

This fixture case reconstructs the remaining gallery disposition after a long CI repair. It preserves the full current run log, failed-test names and prior PR discussion. The executor receives the competing historical diagnosis without the expected assertion in its task. Read and Grep can find the deciding diagnostic.

Run through the shared Promptfoo preparation and capture described in `evals/AGENTS.md`, filtering for `Gallery attribution`. `source.json` hashes each input and fetches the immutable failed-run log through `gh`; the discussion is the pre-report snapshot from the original session. No evidence is installed into consumer plugins.

This is a Claude reconstruction of a Codex decision, not a native continuation of the original session. Earlier compressed and Codex-prefix comparisons passed in both arms. They did not reproduce the wrong report or establish an instruction improvement. Read the saved drafts and skill/tool traces before interpreting any new comparison. The rubric allows an explicitly unproven shared cause; it rejects an established recurrence unsupported by the current assertion.
