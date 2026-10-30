# Parallel maintenance

Daily and weekly sweeps can uncover substantial independent work. Make progress
across it: delegate atomic repairs, such as fixing a failing test, to issues
with enough evidence for their own triagers. Reuse an existing issue or repair
PR when it owns the work. Follow **Recover issue work** in
`/tend-ci-runner:notifications` for unfinished issues; file new issues under
`/tend-ci-runner:open-pr`'s creation allowance and dedup guidance. For CI-report
causes, use `/tend-ci-runner:triage`'s `references/ci-failures.md`.

For work the sweep handles in-process, use subagents extensively: independent
log investigations, PR assessments and repo-specific maintenance can proceed
in parallel. Give each a distinct scope, the relevant skills and evidence,
and its own worktree when editing. Size the fan-out to the independent work
and available resources rather than imposing a fixed small quota. A small or
coupled task may need no delegation. The parent reconciles findings, checks
current ownership before outward actions, and waits for its subagents before
reporting the sweep's outcome.
