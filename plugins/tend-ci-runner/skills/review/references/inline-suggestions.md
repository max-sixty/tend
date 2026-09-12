# Inline suggestions

Depth behind `/tend-ci-runner:review`'s **Submit** step: read when the review carries findings with concrete fixes.

- [Posting the payload](#posting-the-payload)
- [Recovering from inline comment 422 errors](#recovering-from-inline-comment-422-errors)

## Posting the payload

Post inline suggestions via the review API. First compose `$TMPDIR/review-body.md` per **Submit**, then build the payload:

`````bash
cat > "$TMPDIR/review-payload.json" << 'ENDJSON'
{
  "event": "COMMENT",
  "comments": [
    {
      "path": "example/file.txt",
      "line": 3,
      "body": "```suggestion\nnew text here\n```"
    }
  ]
}
ENDJSON

BODY=$(cat "$TMPDIR/review-body.md") || exit 0
REVIEWED=$(cat "$TMPDIR/reviewed-head") || exit 0
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
jq --arg body "$BODY" --arg sha "$REVIEWED" \
  '.body = $body | .commit_id = $sha' "$TMPDIR/review-payload.json" > "$TMPDIR/review-final.json"

/usr/bin/python3 -E -s "${CLAUDE_PLUGIN_ROOT}/scripts/review_preflight.py" post <number> -- \
  gh api "repos/$REPO/pulls/<number>/reviews" \
    --method POST \
    --input "$TMPDIR/review-final.json"
`````

**Do not** use `-f 'comments[0][path]=...'` flag syntax — `gh api` converts array indices to object keys, which GitHub rejects.

- If a review has both suggestions and prose observations, put the suggestions as inline comments and the prose in the review body.
- Multi-line suggestions: set `start_line` and `line` to define the range. GitHub **replaces** every line in that range with the suggestion content — any line in the range that isn't reproduced in the replacement is **deleted**.

  **Before posting any multi-line suggestion, verify it:**

  1. **Read the exact lines** `start_line` through `line` from the diff hunk.
  2. **Diff mentally**: every line in that range must either appear (possibly modified) in the replacement text, or be a line you intend to delete. If any line would be silently dropped, **shrink the range** or include the line in the replacement.
  3. **Cap the range at ~10 lines.** Larger suggestions are error-prone and hard to review. For changes spanning more than 10 lines, split into multiple suggestions or push a fix commit instead.
  4. **Never span markdown fences.** If the range includes a `` ``` `` line, GitHub's suggestion parser may consume it as a delimiter, corrupting the result. Either shrink the range to avoid the fence or push a commit.

## Recovering from inline comment 422 errors

GitHub returns `422 Unprocessable Entity` with "Line could not be resolved" when inline comment line numbers don't map to valid positions in the diff. Two failure modes produce the same error message but differ in whether a review record is persisted:

- **(a) Large / complex diff**: the body is persisted first, then the inline comments are rejected — leaving an **orphan body-only review** on the PR. A blind retry creates a duplicate.
- **(b) Line outside the diff entirely**: the entire POST is rejected up front — **no review is persisted**. Retrying without inline comments is correct; editing a non-existent review will fail.

**Check which case you are in before deciding how to recover** — query for an orphan review on the current HEAD first, then branch on the result.

```bash
# `orphan_id` is the body-bearing bot review anchored here, and only that: a
# synthetic reply container on the same HEAD has no body, so the PUT below
# can't overwrite an unrelated reply, and a body-bearing review from before a
# rewrite is excluded too — it reports `.commit_id == head_sha`, so without
# that filter the PUT destroys a published review, leaving this run's findings
# over the old review's inline comments on code that no longer exists.
ORPHAN_ID=$(uv run --script \
  "${CLAUDE_PLUGIN_ROOT}/scripts/bot_review_state.py" state <number> \
  | jq -r '.orphan_id // empty')
```

Use the printed ID as the literal `<orphan-id>` below; shell variables do not
survive between agent tool calls. Then, in either case, **move the failed inline
comments into the review body** as fenced code blocks with file paths,
preserving the hidden marker when this is a draft review, and:

- **If `ORPHAN_ID` is non-empty (case a)**: edit the existing review instead of creating a duplicate.
  ```bash
  REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
  /usr/bin/python3 -E -s "${CLAUDE_PLUGIN_ROOT}/scripts/review_preflight.py" post <number> \
    --edit-review <orphan-id> -- \
    gh api "repos/$REPO/pulls/<number>/reviews/<orphan-id>" \
      -X PUT -F body=@"$TMPDIR/updated-review-body.md"
  ```
  If the edit itself fails, **do not post another review** — the body-only review is sufficient.

- **If `ORPHAN_ID` is empty (case b)**: retry the `POST` with `comments` omitted (body-only), since no duplicate is possible.
  ```bash
  jq 'del(.comments)' "$TMPDIR/review-final.json" > "$TMPDIR/review-body-only.json"
  REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
  /usr/bin/python3 -E -s "${CLAUDE_PLUGIN_ROOT}/scripts/review_preflight.py" post <number> -- \
    gh api "repos/$REPO/pulls/<number>/reviews" \
      --method POST --input "$TMPDIR/review-body-only.json"
  ```

Prevention: before writing any inline comment, verify the target line falls inside one of the PR's diff hunks. For fixes outside the diff, use the "push a fix commit" path instead of an inline suggestion (**Submit**).
