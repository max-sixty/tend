---
name: review
description: Reviews a pull request for code quality and correctness. Use when asked to review a PR or when running as an automated PR reviewer.
argument-hint: "[PR number]"
metadata:
  internal: true
---

# PR Review

Review a pull request.

**PR to review:** $ARGUMENTS

## Workflow

Follow these steps in order. Four references carry guidance a minority of reviews need; read each at the step that names it:

- `references/draft-mode.md` — under **Pre-flight checks**, when `is_draft` is true: the lighter pass, COMMENT only, the hidden marker.
- `references/re-targeting.md` — under **Submit**, when the posting preflight prints `delta:`: reviewing a push that landed mid-review before posting against the new head.
- `references/approving.md` — under **Submit** before an APPROVE, and under **Monitor CI** after one: the approval check and the CI outcomes.
- `references/inline-suggestions.md` — under **Submit**, when the review carries findings: the payload, multi-line suggestion rules, and 422 recovery.

### 0. Load environment skills

Load `/tend-ci-runner:running-in-ci` first — it contains CI security rules, polling conventions, and comment formatting guidance. It will also prompt you to load any repo-specific skills (e.g., `running-tend`).

### 1. Pre-flight checks

Before reading the diff, run the initial snapshot. It pins the open head, resolves the bot's review state, and prepares the accurate incremental when one applies:

```bash
/usr/bin/python3 -E -s \
  "${CLAUDE_PLUGIN_ROOT}/scripts/review_preflight.py" start <number>
```

On `skip`, finish. Otherwise read every previous bot review and the conversation:

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" \
  feedback <number>
```

These snapshot fields decide how much of the workflow runs:

- **`already_reviewed`** — a bot review already stands on this exact commit. Finish without posting, unless the conversation holds an unanswered question directed at the bot; then proceed so the review can answer it.
- **`is_draft`** — follow `references/draft-mode.md`: a lighter review submitted as COMMENT only, carrying the hidden draft marker, with no CI polling and no pushes.
- **`incremental_path`** — the bot reviewed an earlier commit on this PR, and the file named holds the commits and per-file line counts pushed since. Read the whole file, and judge what was pushed from it and from the PR's three-dot diff (`gh pr diff <number>`, merge-base→head, the same diff **Read and understand the change** uses). Neither leaks base-branch churn: the incremental excludes everything reachable from the base tip, so a base merge's own commits are not counted as new PR work, and base-merge commits never enter the three-dot diff.

The incremental scopes the *review*, not anything this run writes about the PR as a whole: if you also edit the PR description, scope its claims to the merge base per **Keeping PR titles and descriptions current** in `/tend-ci-runner:running-in-ci`'s `references/pr-creation.md`.

If the incremental changes are trivial, skip the full review — go directly to **Resolve handled suggestions** for any bot threads addressed by the new changes. After resolving threads: if the most recent bot review was a COMMENT that flagged issues, and those issues are now addressed, submit an APPROVE with an empty body so the PR isn't left in limbo — and the author-readiness gate under **Submit** applies here too, since these are the bot's own findings closing out rather than the author's. Use the recipe under **Submit**, which pins the commit read here. Otherwise do not submit a new review — the existing one stands. Do NOT proceed to steps 2–7; finish. Rough heuristic: changes under ~20 added+deleted lines that don't introduce new functions, types, or control flow are typically trivial.

**Commit and PR authorship do not affect review behavior.** Apply the same trivial-vs-substantive heuristic regardless of who pushed the new commits. When `tend-notifications` or `tend-ci-fix` pushes a fix to a human-authored PR, reviewing (and re-approving) the updated state is expected — the reviewer role is independent of commit authorship.

**Apply the sibling-workflow dedup rule from `/tend-ci-runner:running-in-ci`'s `references/posting.md`** to both the review body and inline comments. If a prior bot comment in the conversation already covers a point — a previous review on this or an earlier commit, a `tend-mention` reply, a `tend-triage` post, anything from a tend workflow — omit it from this review and stick to diff-grounded findings. If that leaves no new diff-grounded finding on the incremental changes and the only outstanding concern is a still-unresolved thread from an earlier bot review, do not post a new review: that thread already blocks the PR, and restating "the prior thread still applies" on every push is noise. Resolve any bot threads the new commits addressed (**Resolve handled suggestions**), then finish without posting. A fresh review is warranted only when the incremental diff introduces a new finding, or resolves the last open one (then approve with an empty body — the author-readiness gate under **Submit** applies here too, since these are the bot's own findings closing out rather than the author's). When concurrent runs race (a new push while the first run is still responding), both see the same unanswered question — check whether a bot reply exists after the question's timestamp before answering. Address remaining unanswered questions in the review body (not via `gh pr comment`).

### 2. Check for overlapping PRs

Before reading the diff, scan other open PRs for file overlap. If another PR touches the same files with a similar fix, flag it in the review so one can be closed as a duplicate.

### 3. Read and understand the change

1. Read the PR diff with `gh pr diff <number>`.
2. Before going deeper, look at the PR as a reader would — not just the code, but the shape: what files are being added/changed, and does anything look off?
3. Read the changed files in full (not just the diff) to understand context.

### 4. Review

Scale depth to the change. A docs-only PR or a mechanical rename needs a skim for correctness, not the full checklist. A new algorithm or state-management change needs trace analysis. Don't over-analyze trivial changes.

Check the project's instruction files for language-specific review criteria and conventions. Load any project-specific review skill if available.

**Code quality:**

- Is the code clear and well-structured?
- Are there simpler ways to express the same logic?
- Does it avoid unnecessary complexity, feature flags, or compatibility layers?
- In the bot's own automation (its skills, and the scheduled workflows that invoke it) and the repo's CI config: is the change worth its weight? Challenge new mechanism whose only payoff is saved compute — retries, skip-gates, caches, scheduling arithmetic — per **Weighing a Fix** in `/tend-ci-runner:running-in-ci`. Judge the whole change: a PR well-argued line by line can still cost more machinery than the compute it saves, and the review should say so plainly. Recommend the simple knob, or closing the PR, over refinement.

**Correctness:**

- Are there edge cases that aren't handled?
- Could the changes break existing functionality?
- Are error messages helpful and consistent with the project style?
- **Trace failure paths, don't just note error handling exists.** For code that modifies state through multiple fallible steps, walk through what happens when each error fires. What has already been mutated? Is the system left in a recoverable state?

**Testing:**

- Are the changes adequately tested?
- Where a doc comment and the code disagree, the finding is which one is wrong — don't suggest a test that pins the current output, which freezes behavior nobody has called intended.

**Same pattern elsewhere:**

When a PR fixes a bug or changes a pattern, search for the same pattern in other files. If found in the diff, add inline suggestions; if found outside the diff, offer to push a fix commit.

**Citing code outside the diff:**

The checkout uses `refs/pull/N/merge`, so you read the merged tree (PR head + current base branch). When a finding involves code outside `gh pr diff` — code the PR didn't touch but now interacts with — identify the interacting code by semantic anchor (selector, function name, declaration text) and quote enough of the relevant property/value that the reader can grep for it. Don't lead with the line number: the author's local branch may be behind main, so line numbers from the merged tree won't match their checkout, and a line-number-first citation reads as a hallucination when they check the line on their stale branch. Same principle as the `blob/main/...#L42` link rule in `/tend-ci-runner:running-in-ci` — line numbers are fragile across diverging refs.

**Duplication check (for new functions/types):**

For every new public or module-level function added in the diff, search the codebase for existing functions that do the same thing. LLM-generated code frequently reinvents internal APIs — this is the highest-value check for externally contributed PRs.

Two search strategies, both required:

1. **Similar names and signatures.** Search for functions with similar names, return types, or parameter types.
2. **Overlapping subgoals.** Identify the intermediate steps the new code performs and search for existing code that does the same sub-tasks.

Flag duplicates — reuse is almost always better than a parallel implementation.

### 5. Second pass

Run a `/tend-ci-runner:code-review` pass over the PR's merged tree. Every review that reaches this step runs one — trivial diffs included; **Review**'s depth-scaling sets how deep the pass goes, never whether it happens. It's a structured second pass — correctness and cleanup angles, then a verify pass — that returns findings rather than posting anything, and it supplements **Review**'s manual checks rather than replacing them.

Scale its depth to how core the change is:

- Peripheral or mechanical (config, dependency bumps, test-only, docs that don't assert how the code behaves): tell it the change is peripheral, so it runs the short angle set in one pass.
- The project's core logic, or prose asserting how it behaves: tell it the change is core, so it fans the angles out and sweeps for gaps. Prose is checked by reading the code it describes, so a one-line Markdown diff can still be core.

What counts as core is repo-specific; let the project's own instruction files, a repo review skill, or your judgment decide. Both passes feed one verdict: fold its findings into the review **Submit** posts. It only reports back — it never posts a review, comment, or commit of its own, so the dedup and single-review path is preserved. Its findings are not the review: when it returns, continue to **Submit**.

### 6. Submit

**For a review that reached the second pass, before submitting, say what that pass returned** — its confirmed findings, or "no findings". That statement is a compliance check: say it in the session, not the review body, so an empty-body APPROVE stays empty. The findings themselves still get folded into the review, per **Second pass**. If you can't say, the pass didn't run — go back to **Second pass** and run it. A full review that reaches this point without it is not submittable. The trivial-increment and dedup close-out paths under **Pre-flight checks** deliberately skip steps 2–7 and are exempt.

**If there are no issues, approve with an empty body — silence means correct.**

**Unless the author withheld merge readiness.** When the PR body — or a later comment from the author or a maintainer — says the change should not merge yet — "should not merge until…", "not ready", design questions the author calls unresolved — the verdict is withheld the same way the draft flag withholds it, and plenty of contributors state it in prose rather than toggling draft. Submit COMMENT instead, naming the stated blocker that holds the verdict; name it once, and on a later pass that finds nothing new stay silent rather than restating it — the surrounding dedup rules are keyed on threads, so they don't reach a body-only COMMENT. Your own findings being closed out does not clear it: "everything the reviewer raised is fixed" and "the author says this must not merge" are independent conditions, and only whoever stated the blocker retracts it. The asymmetry is why this is worth a condition: withholding a warranted approval costs a re-review on the next push, while an APPROVE standing on a PR its author gated is a wrong outward signal that persists until someone notices.

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
# Read the sha first and bail if it isn't there: inlined as `$(cat ...)` a
# missing file substitutes the empty string and the POST still runs, which is
# the unpinned review this pins against.
REVIEWED=$(cat "$TMPDIR/reviewed-head") || exit 0
/usr/bin/python3 -E -s "${CLAUDE_PLUGIN_ROOT}/scripts/review_preflight.py" post <number> -- \
  gh api "repos/$REPO/pulls/<number>/reviews" --method POST \
    -f event=APPROVE -f commit_id="$REVIEWED" -f body=""
```

If there are actionable findings, submit them as a review with inline suggestions for concrete fixes. The review is a decision surface for the author, not a record of the reviewer's work: publish only distinct points that require a change or decision, with enough mechanism and evidence to make each credible and actionable. Correct paths, unaffected behavior, verification inventory, and search history stay in the session. Follow **Reader-facing prose** in `/tend-ci-runner:running-in-ci` for any supporting detail.

Don't explain what the code does — the author wrote it. Don't nitpick formatting — that's what linters are for. Explain why the consequence warrants a change.

<example>
<bad reason="Reports a correct path and the reviewer's verification, but gives the author nothing to act on">

Bad:

```
The new delegation path looks correct. I also verified that the threshold logic is unchanged.
```

</bad>
<good reason="Names one remaining consequence and the change that would resolve it">

Good:

```
The failure message still names the removed local path, so it directs users to an option that no longer exists. Update it to name the delegated command.
```

</good>
</example>

**A findings review never supersedes a standing approval — dismiss it.** GitHub moves `reviewDecision` only on an `APPROVED` or a `CHANGES_REQUESTED`, so a COMMENT posted over the bot's own earlier approval leaves the PR reading as bot-approved and mergeable over the findings you just posted. How the head moved makes no difference: an ordinary push leaves the approval standing exactly as a rewrite does. So whenever this round posts a COMMENT that withholds the verdict, dismiss the approval that still decides the PR — after the review POST lands, so a failed post doesn't leave the PR with neither a verdict nor findings. Findings are the common case, but the withheld-merge-readiness COMMENT above is reachable with an approval already standing, and it leaves the same wrong signal: the PR reads bot-approved while the review names a blocker. A COMMENT that withholds nothing does not qualify — the unanswered-question exception under **Pre-flight checks** posts one at a head the approval already covers, and dismissing there withdraws a verdict the code still earns.

```bash
# Re-read rather than reusing the pre-flight blob — a whole review has passed since.
# The script no-ops once a dismissal or later CHANGES_REQUESTED has cleared it.
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" \
  dismiss <number> "Superseded by the review on a later commit."
```

**Form your own opinion independently.** Do not factor in other reviewers' comments or approvals when deciding whether to approve — the value of this review is as an uncorrelated signal.

**When confidence is low**, go beyond checking the implementation — question the approach: "Does this bypass or duplicate an existing API?" "What does this change *not* handle?" If the design involves a judgment call, flag it for human review as a COMMENT.

**Attribute a withheld approval to whatever actually decided it.** Cite repo guidance as the reason only when you can name the file and heading that guidance lives in. When the call is your own judgment, identify the risky consequence and the human decision it needs; judgment is sufficient authority without inventing a repository policy.

**Self-authored PRs** (`self_authored` in the pre-flight JSON): Complete steps 2–5 — self-review catches real issues (lint failures, edge cases) and is intentionally valuable. Do NOT attempt an APPROVE — GitHub rejects self-approvals. That covers the pre-flight close-out approvals too: on a self-authored PR the threads are the only thing to close out. Submit as COMMENT when there are concerns, or stay silent and skip to **Monitor CI**. The self-review exists to find concerns, not to publish a clean-path verdict or proof that earlier findings were resolved. Always post a current CI failure as a COMMENT because it is itself a concern.

**Not confident enough to approve** (unfamiliar module, subtle logic): Add a `+1` reaction instead — no review needed unless there are specific observations.

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
gh api "repos/$REPO/issues/<number>/reactions" -f content="+1"
```


#### Posting

Before composing the final payload, run the preflight without a command. It checks the PR is open, re-targets onto a newer descendant head, and stops duplicate reviews:

```bash
/usr/bin/python3 -E -s \
  "${CLAUDE_PLUGIN_ROOT}/scripts/review_preflight.py" post <number>
```

On `skip`, post nothing and finish. A re-targeted result prints `delta: <path>` and updates `$TMPDIR/reviewed-head`: follow `references/re-targeting.md` before posting, then run the preflight again. Do not post from the re-targeting pass.

A non-zero exit from this commandless check means nothing was decided. Fix the
error and re-run it. In command mode below, `post:` means the outward command
was attempted; handle that command's failure directly. For a review POST that
returns 422, use the recovery procedure in `references/inline-suggestions.md` instead of blindly
rerunning the preflight.

Every review POST passes its `gh api` command to the preflight after `--`, which runs it only if the pinned head is still open and unreviewed. In that mode the preflight does not re-target.

**Pin every review to the commit you read** — `commit_id` in every posting recipe, read back from `$TMPDIR/reviewed-head`, which **Pre-flight checks** wrote and the posting preflight rewrites if HEAD moved. Two things depend on the pin. GitHub otherwise anchors the review at whatever is live when the POST lands, so the review claims code this session never saw. And the anchor is what the next run's pre-flight reads as `already_reviewed`: pinned to the head you re-targeted onto, the queued run finds that head already reviewed and finishes without posting a second review of the same code.

**Before APPROVE specifically**, run the approval check in `references/approving.md` — a real red or an author-stated blocker withholds the approval.

Post at most one review per run. Give a verdict (**approve** or **comment**, never "request changes") when this pass has something to say: a new diff-grounded finding, or an approval because the last open concern is now resolved. If the dedup rule above left nothing new and a prior unresolved bot thread still stands, post nothing; the earlier review remains the active verdict. Post reviews through the reviews endpoint, not `gh pr comment`. Note: a COMMENT review requires a non-empty body — if there's nothing to say and no prior concern stands, use the approve-with-empty-body pattern.

**Inline suggestions are mandatory for concrete fixes.** Whenever there's a concrete fix (typos, doc updates, naming, missing imports, minor refactors, test additions), post it as an inline suggestion on the exact line — never as a code block in the review body. Inline suggestions let the author apply with one click; code blocks force them to find the line and copy-paste manually.

For fixes targeting lines outside the diff, offer to push a fix commit instead.

Build the review payload — inline comments, `commit_id`, the preflight-wrapped POST — per `references/inline-suggestions.md`, which also carries the multi-line suggestion rules and the 422 recovery.

### 7. Monitor CI

If you **stayed silent** (no review posted, nothing to dismiss), finish — there's no follow-up gated on the CI result. Don't background-poll: per `/tend-ci-runner:running-in-ci` under "End the turn only when work is shipped", the completion notification isn't reliably delivered to a CI session.

If you **approved**, the dismissal-on-failure is a gated follow-up. Poll in the foreground per `/tend-ci-runner:running-in-ci`'s `references/ci-monitoring.md`, pinned to `$TMPDIR/reviewed-head`, then handle the outcome per **After the approval** in `references/approving.md`. If the PR head moves while polling, stop polling the stale commit; the queued review handles the new HEAD.

### 8. Resolve handled suggestions

After submitting the review, check if any unresolved bot threads have been addressed by the new changes. Resolve threads where the suggestion was applied.

**Only resolve if the substance was addressed.** Read both the suggestion and the new code — if the author took a different approach, verify its technical accuracy before resolving. "Different wording" is not "addressed" when the new wording is less accurate than the suggestion. When in doubt, leave the thread open for a human reviewer.

**Self-authored PRs are especially risky.** When the bot is both author and reviewer, there is a bias toward accepting the code's own claims. Treat self-authored thread resolution with extra skepticism — read the code and verify the claim independently rather than trusting the doc comment or commit message.

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" \
  threads <number>
# For each thread whose substance you verified as addressed:
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" \
  resolve-thread <thread-id>
```

Outdated comments (null line) are best-effort — skip if the original context can't be located.

### 9. Push fixes

Pushing to the branch under review fires `synchronize`, which queues another run behind this session rather than cancelling it. Submit the review (**Submit**) and resolve threads (**Resolve handled suggestions**) before pushing, so the review documents the code the fix responds to. Batch every fix into a single push; each push costs another review round. Poll the pushed fix's CI to green per `/tend-ci-runner:running-in-ci`'s `references/ci-monitoring.md` before ending the session; the queued run reviews the new HEAD.

Leave the review pinned to the head you reviewed. **Submit**'s re-targeting is for pushes by others during the review; re-target onto your own fix and the queued run reads that head as already reviewed, then finishes without looking at the fix. Reaching a clean pass is that run's job: it reviews the pushed head, resolves the threads the fix addressed, and fixes whatever it finds new, until a pass finds nothing.

**PRs with no human author** (this bot's own, and third-party bot PRs like Dependabot or renovate): Nobody else will act on the feedback — a third-party bot doesn't read it, and on your own PR you are the author. A review that only describes the fix leaves the PR red and pushes the work onto a maintainer — the opposite of the point. If you can articulate the fix, apply it: commit and push it to the PR branch. "Not a one-token change" and "more than one syntactically valid form exists" are **not** reasons to defer — pick the option most consistent with the surrounding code and the repo's existing conventions, push it, and note any alternative in the review. The only bar for deferring is that *no defensible default exists*: a genuine semantic ambiguity that needs maintainer intent, not merely a fix that took thought to derive. If the review already worked out the answer, that answer is pushable. Rebase onto the latest target branch first if the branch is behind.

**Human PRs**: Post inline suggestions first. Additionally, offer to push a commit when the fixes are mechanical and correctness is obvious. Only push after the author accepts.

```bash
gh pr checkout <number>
git add <files>
git commit -m "fix: <description>"
git push
```
