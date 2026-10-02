# CI failure attribution

This case asks for a CI disposition with competing evidence: an earlier gallery
Undo diagnosis and a current traceback from the same feature. It checks the
reported failure and its attribution, leaving the wording and routing open.
The fixtures retain the fetched PR discussion before the false report and the
deciding traceback with trailing whitespace stripped. `source.json` records
their provenance and hashes.

Run from the repository root:

```bash
claude plugin eval plugins/tend-ci-runner --case ci-failure-attribution \
  --ablation none --allow-tools Write --judge-model sonnet \
  --keep-temp --no-publish --trust-plugin \
  --output-dir .tmp/evals/ci-failure-attribution
```

Inspect `aggregate-result.json`, the captured draft and the executor trace.
Confirm it loaded the tested skills and read both evidence files; execution
errors do not count as behavioral passes. Use a separately staged historical
plugin for comparisons; a no-plugin arm does not measure the instruction edit.

This is a compressed reconstruction executed by Claude, not a replay of the
original Codex session. Earlier baseline and candidate runs both reported the
correct assertion. The case is retained at the maintainer's request to check
future regressions; passing it does not demonstrate that the instruction change
corrects the original production failure.
