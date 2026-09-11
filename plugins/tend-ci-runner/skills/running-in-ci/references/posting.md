# Posting comments and replies

Depth behind `/tend-ci-runner:running-in-ci`: read before composing or posting any comment, review body, inline reply, PR body, or issue body.

- [Recheck Before Posting](#recheck-before-posting)
- [Replying to comments](#replying-to-comments)
- [Mechanics](#mechanics)

## Recheck Before Posting

**Before posting any comment, review, or inline reply**, re-fetch the conversation and check whether the response would duplicate something already there. Run the re-fetch **as the last step before the post**, the same way the `gh pr create` dedup in `references/pr-creation.md` does — composing the body, grepping it for placeholders, and checking its links all take time, and a sibling's comment landing in that gap is invisible to a check that ran before them. Two duplication paths:

- **New entries arrived during the session.** Other participants may comment while the bot works. Compare counts against what was read at session start.
- **A sibling tend workflow already responded.** Every workflow posts as the same bot account, so the pre-empting comment can come from an event-triggered run (`tend-mention`, `tend-triage`, `tend-review`) or from a scheduled sweep that reaches the same thread (`tend-nightly`, `tend-review-runs`, `tend-notifications`, plus any non-`tend-*` workflow the repo's `running-tend` skill lists). A freshly-opened issue is the sharpest case: `tend-triage` fires on `issues: opened` and owns it, while a sweep already in session may find the same issue and answer it independently. The earlier comment may already be in the conversation at session start, so a stale-count check alone is not enough — scan for prior bot comments newer than the maintainer message being responded to.

```bash
# For issues
gh issue view <number> --json comments --jq '.comments | length'

# For PRs (comments + reviews)
gh pr view <number> --json comments,reviews \
  --jq '{comments: (.comments | length), reviews: (.reviews | length)}'
```

Keep `reviews` in the PR projection rather than narrowing to `comments` — it is the entry a dedup-shaped check is most likely to drop, and on a fork PR it is the one nothing else will pick up (see **A review that lands while you poll is not yours to action** in `references/ci-monitoring.md`).

If any prior entry — from a human or another tend workflow — already addresses a point the response would make, omit that point. The dedup applies equally to comment bodies, review bodies, and inline replies. If the response is now entirely redundant, don't post it.

If the author resolved the issue, acknowledge it rather than post stale analysis. If new information contradicts the findings, update before posting.

**A new entry may be a directive, not a duplicate.** The re-fetch above guards against redundant posts, but a comment that arrived while you worked can also be a maintainer follow-up that *changes the work* — a second instruction, a correction, a narrowed scope. The window is widest after a long edit→commit→push sequence: minutes pass between the session-start read and the post, and that gap is exactly when a maintainer adds to the thread. So the re-fetch isn't only a dedup check — read what landed, and if it's a new directive, fold it into the same run rather than shipping a reply (or a commit) against the stale instruction. Treating the task as done is itself a kind of post: re-fetch before ending the turn, not only before commenting.

## Replying to comments

Reply in context rather than creating new top-level comments:

- **Inline review comments** (`#discussion_r`): To read a single review comment, use the comment ID **without** the PR number in the path:
  ```bash
  gh api repos/{owner}/{repo}/pulls/comments/{comment_id}
  ```
  To reply:
  ```bash
  cat > "$TMPDIR/reply.md" << 'EOF'
  Your response here
  EOF
  gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
    -F body=@"$TMPDIR/reply.md"
  ```

- **Review events with inline comments** (review ID in prompt): A review may include inline comments. Fetch them by review ID and reply to each individually:
  ```bash
  gh api repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/comments \
    --jq '.[] | {id: .id, path: .path, body: .body}'
  ```
  Reply to each comment using the inline review comment reply endpoint above.

- **Conversation comments** (`#issuecomment-`): Post a regular comment (GitHub doesn't support threading).

### Dedup check for inline review comment replies

A single PR review can fire both `pull_request_review` and `pull_request_review_comment` events, triggering separate workflow runs (serialized by the concurrency group, not truly concurrent). Before replying to an inline review comment, check whether the bot already replied:

```bash
BOT_LOGIN=$(gh api user --jq '.login')
EXISTING=$(gh api "repos/{owner}/{repo}/pulls/{number}/comments?per_page=100" \
  --jq "[.[] | select(.in_reply_to_id == {comment_id} and .user.login == \"$BOT_LOGIN\")] | length")
```

If `EXISTING` is greater than 0, **do not post** — another run already handled this comment. Exit silently.

## Mechanics

**Write bodies to a file, then post with `--body-file`.** The composed file is reviewable before it ships, quoting and escaping are non-issues, and line wrapping is just file content. Put the file under `$TMPDIR`. `--body "…"` is fine only for a one-line body containing no backtick, `$`, or `\`. Inside double quotes bash runs a backticked span as a command and substitutes its output, so a markdown inline-code span is silently deleted from the posted comment: `` --body "`some-check` now passes" `` ships as ` now passes`. Inline code appears in nearly every body the bot writes, and single-quoting instead breaks on any apostrophe, so reach for `--body-file` whenever the text is anything but plain prose.

```bash
# After writing $TMPDIR/comment-body.md:
gh issue comment "$ISSUE" --body-file "$TMPDIR/comment-body.md"
```

**Line wrapping:** GitHub renders newlines literally in issue bodies, PR descriptions, and comments — a line break in the source becomes a `<br>` in the output, so a paragraph hard-wrapped at ~72 chars ships with mid-sentence breaks. Write each paragraph as a single long line and let the browser reflow. Code blocks, bullet lists, and tables keep their newlines as-is.

Each CI run is a fresh session with no memory of prior reasoning, so preserve evidence another run would need to resume the thread without re-deriving it. Curate that durable record for future readers too.

Always use markdown links for files, issues, PRs, and docs. **Any link containing `#L` must use a commit SHA, never `blob/main/...#L42`** — line numbers shift silently, so the link stays valid but starts pointing at different code than the comment describes. Get the SHA with `git rev-parse HEAD` before composing the link.

**Check the body's links before posting it.** Run this over every composed body — comment, PR body, issue body — as part of the pre-post pass, and fix what it names:

```bash
uv run --script \
  "${CLAUDE_PLUGIN_ROOT}/scripts/check_body_links.py" "$TMPDIR/comment-body.md"
```

It resolves every 40-hex SHA in the body against the API and reports any `#L` anchor pinned to a branch or an abbreviation. Resolving is the part a scan by eye cannot do: a hand-typed OID is well-formed whether or not the commit exists, so a fabricated SHA — the model extending an abbreviation it saw in `git log` instead of running `git rev-parse HEAD` — reads as correctly pinned and ships a permalink that 404s. Run it after the push when the body cites a commit from this session; before the push that commit is unreachable and reports as dead, correctly.

**Owners it cannot check — read `$GITHUB_REPOSITORY` from the environment, don't hand-type the owner.** The model reliably guesses wrong — past comments have shipped with the wrong owner (e.g. `anthropics/<repo>` on a repo not owned by Anthropic). The script catches a wrong owner on a SHA-pinned link, because that URL does not resolve either; on every other link, scan the body's `github.com/` hits and confirm each owner is either `$GITHUB_REPOSITORY` or a repo the text genuinely means.

**Authoring fenced bodies with backticks.** When a body contains a fenced code block, the model often defensively escapes the inner fence (`` \`\`\`bash ``) "to prevent it from closing the outer fence early"; the same instinct can produce `` \`foo\` `` for inline spans. Those backslashes survive into the rendered body as literal `\` characters. Author with bare backticks. For nested fenced blocks, use a **longer outer fence** — four or five backticks outside, three inside — so the inner three-backtick fence renders intact without escaping. Writing the body to a file preserves data verbatim and removes shell quoting from the equation.

- **File-level link (no `#L` anchor)**: `blob/main/src/foo.rs` is fine
- **Line reference**: `blob/<sha>/src/foo.rs#L42` — commit SHA required, never `blob/main/...#L42`
- **Issues/PRs**: `#123` shorthand
- **External**: `[text](url)` format

Don't add job links, footers, or authorship sign-offs (e.g. `> _Written by an agent on behalf of @maintainer_`) — the bot account already conveys authorship, and the harness suppresses the client's default footer. This covers PR and issue bodies too, not just comments.
