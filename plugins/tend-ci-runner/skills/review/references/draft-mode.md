# Draft mode

Depth behind `/tend-ci-runner:review`'s **Pre-flight checks** step: read when the preflight reports `is_draft` as true.

## Lighter review, COMMENT only

If `is_draft` is true, run a lighter review:

- Skip **Check for overlapping PRs** — landing-readiness concern, premature for WIP.
- Skip the duplication scan in **Review** — the author is still shaping the design.
- Submit as **COMMENT only**, never APPROVE. GitHub blocks approving drafts, and the author hasn't asked for a verdict yet.
- Make the review's context clear: this is feedback on work in progress, not a merge verdict, and the author can mark it ready to request the full review.
- Include the exact hidden marker `<!-- tend:draft-review -->` anywhere in the review body. The posting preflight uses it to replace this COMMENT with a full verdict when the PR becomes ready; it is not part of the reader-facing prose. Carry it through any body you recompose — re-targeting after a mid-review push and the 422 body-only retry both rewrite the body, and dropping the marker there forfeits the replacement silently.
- Skip **Monitor CI** — drafts churn; CI failures are the author's to chase.
- Skip **Push fixes** — never push to a WIP branch.

**Pre-flight checks**, **Read and understand the change**, **Review** (without the duplication scan), **Second pass**, **Submit** (COMMENT path), and **Resolve handled suggestions** still apply. Stay silent if there's nothing actionable; don't post a "looks fine" comment.
