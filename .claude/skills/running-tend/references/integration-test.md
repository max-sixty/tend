# Weekly integration test

Create a fresh public repo under `tend-agent`, install tend on it, drive a
real issue and a real PR, assert the bot responded, and delete the repo.

`$GITHUB_TOKEN` (bot PAT) and `$CLAUDE_CODE_OAUTH_TOKEN` are both present
in the agent's env — claude-code-action propagates them, and the action's
`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0` lets bash inherit them. `gh`
authenticates as `tend-agent` automatically.

Run steps in order. If any step from §2 to §8 fails, jump to §9 (cleanup),
then §10 (report). Do not skip cleanup.

## 1. Setup

```bash
TEST_REPO="tend-agent/tend-integration-$(date -u +%Y%m%d)"
WORK="/tmp/$(basename "$TEST_REPO")"
# A prior run that crashed before cleanup will collide on create; wipe it.
gh repo delete "$TEST_REPO" --yes 2>/dev/null || true
rm -rf "$WORK"
```

## 2. Create the test repo

```bash
gh repo create "$TEST_REPO" --public --add-readme
gh repo clone "$TEST_REPO" "$WORK"
cd "$WORK"
```

## 3. Seed secrets

```bash
printf '%s' "$GITHUB_TOKEN" | gh secret set TEND_BOT_TOKEN --repo "$TEST_REPO"
printf '%s' "$CLAUDE_CODE_OAUTH_TOKEN" | gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo "$TEST_REPO"
```

## 4. Open the install PR

Only `triage` and `review` are exercised; the rest are disabled to keep the
test fast and to avoid `mention` self-triggering on the bot's own review
comment.

```bash
mkdir -p .config
cat > .config/tend.yaml <<'EOF'
bot_name: tend-agent
harness: claude
workflows:
  mention: false
  notifications: false
  ci-fix: false
  nightly: false
  review-runs: false
  weekly: false
EOF

uvx tend@latest init --with-install-test
git checkout -b install-tend
git add .
git commit -m "chore: install tend"
git push -u origin install-tend
gh pr create --title "Install tend" \
  --body "Integration test install PR." \
  --base main --head install-tend
PR=$(gh pr view --json number --jq .number)
```

## 5. Verify tend-install-test, then merge

Poll up to 10 min. Failing conclusion or absence of a run = test failure.

```bash
for _ in $(seq 1 60); do
  read -r status conclusion < <(gh run list --repo "$TEST_REPO" \
    --workflow tend-install-test --branch install-tend --limit 1 \
    --json status,conclusion --jq '.[] | "\(.status) \(.conclusion)"')
  [ "$status" = "completed" ] && break
  sleep 10
done
[ "$conclusion" = "success" ] || { echo "tend-install-test: $status/$conclusion"; exit 1; }

gh pr merge "$PR" --repo "$TEST_REPO" --squash --admin --delete-branch
```

## 6. Set branch protection

The install-test moment is past; run the rest under production-like
protection. The bot is admin and so can bypass — that's a known property
of the security model (see `docs/security-model.md`).

```bash
gh api -X PUT "repos/$TEST_REPO/branches/main/protection" --input - <<'EOF'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 1},
  "restrictions": null
}
EOF
```

## 7. Verify tend-triage

```bash
ISSUE=$(gh issue create --repo "$TEST_REPO" \
  --title "integration test: triage me" \
  --body "Automated integration test. Briefly triage; the repo will be deleted shortly." \
  | grep -oE '[0-9]+$')

RUN_ID=""
for _ in $(seq 1 24); do
  RUN_ID=$(gh run list --repo "$TEST_REPO" --workflow tend-triage --limit 1 \
    --json databaseId --jq '.[0].databaseId // empty')
  [ -n "$RUN_ID" ] && break
  sleep 5
done
[ -n "$RUN_ID" ] || { echo "tend-triage: no run created"; exit 1; }
gh run watch "$RUN_ID" --repo "$TEST_REPO" --exit-status

COMMENTS=$(gh issue view "$ISSUE" --repo "$TEST_REPO" --json comments \
  --jq '[.comments[] | select(.author.login == "tend-agent")] | length')
[ "$COMMENTS" -ge 1 ] || { echo "tend-triage: no bot comment on issue #$ISSUE"; exit 1; }
```

## 8. Verify tend-review

```bash
git checkout main && git pull
git checkout -b integration-test-review
printf '\n(integration test edit)\n' >> README.md
git add README.md
git commit -m "chore: trivial edit"
git push -u origin integration-test-review
gh pr create --repo "$TEST_REPO" \
  --title "integration test: review me" \
  --body "Automated integration test PR." \
  --base main --head integration-test-review
PR=$(gh pr view --repo "$TEST_REPO" --json number --jq .number)

RUN_ID=""
for _ in $(seq 1 24); do
  RUN_ID=$(gh run list --repo "$TEST_REPO" --workflow tend-review --limit 1 \
    --json databaseId --jq '.[0].databaseId // empty')
  [ -n "$RUN_ID" ] && break
  sleep 5
done
[ -n "$RUN_ID" ] || { echo "tend-review: no run created"; exit 1; }
gh run watch "$RUN_ID" --repo "$TEST_REPO" --exit-status

# Self-authored PRs get COMMENT, not APPROVE — match either.
REVIEWS=$(gh pr view "$PR" --repo "$TEST_REPO" --json reviews \
  --jq '[.reviews[] | select(.author.login == "tend-agent")] | length')
[ "$REVIEWS" -ge 1 ] || { echo "tend-review: no bot review on PR #$PR"; exit 1; }
```

## 9. Cleanup (always)

```bash
cd /
gh repo delete "$TEST_REPO" --yes 2>/dev/null || true
rm -rf "$WORK"
```

## 10. Report failure

If any step from §2 to §8 failed, open a labeled issue in `max-sixty/tend`.
The label is created on demand so the first failure works without prior
setup.

````bash
gh label create integration-test-failure --color B60205 \
  --repo max-sixty/tend 2>/dev/null || true

RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
gh issue create --repo max-sixty/tend \
  --title "Weekly integration test failed" \
  --label integration-test-failure \
  --body "Run: $RUN_URL

Failed at <step>. Captured output:

\`\`\`
<paste the failing command's stderr and any relevant gh run URLs>
\`\`\`"
````

Include the test repo's failing workflow run URL in the body if the
failure was in §5, §7, or §8 (capture it before §9 deletes the repo).
