# CI failure attribution

This case asks for a CI disposition with competing evidence: an earlier gallery
Undo diagnosis and a current traceback from the same feature. It checks the
reported failure and its attribution, leaving the wording and routing open.
The fixtures retain the fetched PR discussion before the false report and the
deciding traceback with trailing whitespace stripped. `source.json` records
their provenance and hashes.

Run from the repository root:

```bash
attribution_stage=$(mktemp -d)
mkdir -p "$attribution_stage/plugins" "$attribution_stage/evals"
cp -R plugins/tend-ci-runner "$attribution_stage/plugins/"
cp -R evals/ci-failure-attribution "$attribution_stage/evals/"
claude plugin eval "$attribution_stage" --case ci-failure-attribution \
  --ablation none --allow-tools Write --judge-model sonnet \
  --keep-temp --no-publish --trust-plugin \
  --output-dir .tmp/evals/ci-failure-attribution
```

Inspect `aggregate-result.json`, the captured draft and the executor trace.
Confirm it loaded the tested skills and read both evidence files; execution
errors do not count as behavioral passes. Staging keeps the plugin and fixtures
inside the native runner's containment root and excludes unrelated scratch
cases. For comparisons, stage a historical plugin in the same layout. A
no-plugin arm does not measure the instruction edit.

This is a compressed reconstruction executed by Claude, not a replay of the
original Codex session. Earlier baseline and candidate runs both reported the
correct assertion. The case is retained at the maintainer's request to check
future regressions; passing it does not demonstrate that the instruction change
corrects the original production failure.

The transcript-based preparation in `../prepare.py` does not consume this
fixture-only native case. The command above stages it separately; no evidence
is copied into the installed plugin.
