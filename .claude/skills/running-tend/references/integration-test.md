# Weekly integration test

Drives a real issue and a real PR against a persistent test repo, asserts
`tend-triage` and `tend-review` responded, and resets the repo for the
next week.

## Safety — read first

This recipe issues destructive operations (close issues, close PRs,
delete branches, overwrite secrets) against **exactly one** repo:
`tend-agent/tend-integration`. The literal string `tend-agent/tend-integration`
appears as `--repo` argument on every destructive call below — not a
variable. **Do not substitute a variable**, do not rename the repo, do
not run this recipe against any other repo.

If `tend-agent/tend-integration` does not exist yet, the §1 bootstrap
creates it. Once it exists, subsequent weekly runs only operate on it.

`$GITHUB_TOKEN` (bot PAT) and `$CLAUDE_CODE_OAUTH_TOKEN` are both present
in the agent's env, set on the claude step in `action.yaml`. `gh`
authenticates as `tend-agent` via `$GITHUB_TOKEN` automatically.

The bot PAT needs `workflow` (to push generated workflow files) but
**does not** need `delete_repo` — the recipe never deletes the test
repo; it resets in place.

Run steps in order. If §3, §4, or §5 fails, jump to §6 (reset), then §7
(report).

## 1. Bootstrap (first run only — idempotent on subsequent runs)

Create the test repo if missing, with workflows installed on `main` and
both secrets set. This block is a no-op once the repo exists.

```bash
if ! gh repo view tend-agent/tend-integration --json name >/dev/null 2>&1; then
  gh repo create tend-agent/tend-integration --public --add-readme

  WORK=$(mktemp -d)
  gh repo clone tend-agent/tend-integration "$WORK"
  cd "$WORK"

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

  uvx tend@latest init
  gh auth setup-git
  git add .
  git commit -m "chore: install tend (integration-test bootstrap)"
  git push origin main

  cd - >/dev/null
  rm -rf "$WORK"
fi

# Always (re)seed secrets — handles OAuth-token rotation in the source repo.
printf '%s' "$GITHUB_TOKEN" \
  | gh secret set TEND_BOT_TOKEN --repo tend-agent/tend-integration
printf '%s' "$CLAUDE_CODE_OAUTH_TOKEN" \
  | gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo tend-agent/tend-integration
```

## 2. Reset to a known-clean state

Close any leftover issues/PRs from prior runs, delete any leftover
branches. `main` is never touched.

```bash
for n in $(gh issue list --repo tend-agent/tend-integration \
            --state open --json number --jq '.[].number'); do
  gh issue close "$n" --repo tend-agent/tend-integration \
    --comment "Cleaned up by weekly integration-test reset."
done

for n in $(gh pr list --repo tend-agent/tend-integration \
            --state open --json number --jq '.[].number'); do
  gh pr close "$n" --repo tend-agent/tend-integration \
    --delete-branch \
    --comment "Cleaned up by weekly integration-test reset."
done

# Any branches still hanging around (orphaned by a crashed prior run).
for b in $(gh api repos/tend-agent/tend-integration/branches \
             --jq '.[].name' | grep -v '^main$' || true); do
  gh api -X DELETE "repos/tend-agent/tend-integration/git/refs/heads/$b"
done
```

## 3. Verify tend-triage

Open a fresh test issue, wait for `tend-triage` to register and finish,
assert the bot commented.

```bash
TS=$(date -u +%Y%m%d-%H%M%S)
ISSUE_URL=$(gh issue create --repo tend-agent/tend-integration \
  --title "integration-test triage $TS" \
  --body "Automated weekly integration test. The bot's reply confirms tend-triage is working; the reset step will close this.")
ISSUE=${ISSUE_URL##*/}

RUN_ID=""
for _ in $(seq 1 24); do
  RUN_ID=$(gh run list --repo tend-agent/tend-integration \
    --workflow tend-triage --limit 1 \
    --json databaseId --jq '.[0].databaseId // empty')
  [ -n "$RUN_ID" ] && break
  sleep 5
done
[ -n "$RUN_ID" ] || { echo "tend-triage: workflow run never registered"; exit 1; }

for _ in $(seq 1 60); do
  read -r status conclusion < <(gh run view "$RUN_ID" \
    --repo tend-agent/tend-integration \
    --json status,conclusion --jq '"\(.status) \(.conclusion // "")"')
  [ "$status" = "completed" ] && break
  sleep 10
done
[ "$conclusion" = "success" ] || { echo "tend-triage: $status/$conclusion"; exit 1; }

COMMENTS=$(gh issue view "$ISSUE" --repo tend-agent/tend-integration \
  --json comments --jq '[.comments[] | select(.author.login == "tend-agent")] | length')
[ "$COMMENTS" -ge 1 ] || { echo "tend-triage: no bot comment on issue #$ISSUE"; exit 1; }
```

## 4. Verify tend-review

Clone, create a branch with a trivial README edit, open a PR, wait for
`tend-review` to register and finish, assert the bot reviewed.

```bash
WORK=$(mktemp -d)
gh repo clone tend-agent/tend-integration "$WORK"
cd "$WORK"

BRANCH="integration-test-review-$TS"
git checkout -b "$BRANCH"
printf '\n(integration-test edit %s)\n' "$TS" >> README.md
git add README.md
git commit -m "chore: integration-test trivial edit"
gh auth setup-git
git push -u origin "$BRANCH"

PR_URL=$(gh pr create --repo tend-agent/tend-integration \
  --title "integration-test review $TS" \
  --body "Automated weekly integration test. The bot's review confirms tend-review is working; the reset step will close this." \
  --base main --head "$BRANCH")
PR=${PR_URL##*/}

RUN_ID=""
for _ in $(seq 1 24); do
  RUN_ID=$(gh run list --repo tend-agent/tend-integration \
    --workflow tend-review --limit 1 \
    --json databaseId --jq '.[0].databaseId // empty')
  [ -n "$RUN_ID" ] && break
  sleep 5
done
[ -n "$RUN_ID" ] || { echo "tend-review: workflow run never registered"; exit 1; }

for _ in $(seq 1 60); do
  read -r status conclusion < <(gh run view "$RUN_ID" \
    --repo tend-agent/tend-integration \
    --json status,conclusion --jq '"\(.status) \(.conclusion // "")"')
  [ "$status" = "completed" ] && break
  sleep 10
done
[ "$conclusion" = "success" ] || { echo "tend-review: $status/$conclusion"; exit 1; }

# Self-authored PRs come back as COMMENTED rather than APPROVED — count either.
REVIEWS=$(gh pr view "$PR" --repo tend-agent/tend-integration \
  --json reviews --jq '[.reviews[] | select(.author.login == "tend-agent")] | length')
[ "$REVIEWS" -ge 1 ] || { echo "tend-review: no bot review on PR #$PR"; exit 1; }

cd - >/dev/null
rm -rf "$WORK"
```

## 5. Verify generator drift (lightweight)

Catch generator regressions without round-tripping through the
install-test workflow: re-run the generator against the committed
config and assert no diff.

```bash
WORK=$(mktemp -d)
gh repo clone tend-agent/tend-integration "$WORK"
cd "$WORK"
uvx tend@latest init
if ! git diff --quiet .github/workflows/; then
  echo "tend-integration drift: $(git diff --stat .github/workflows/)"
  exit 1
fi
cd - >/dev/null
rm -rf "$WORK"
```

## 6. Reset (always — even on failure)

Same as §2; run again to close anything created in §3/§4.

```bash
for n in $(gh issue list --repo tend-agent/tend-integration \
            --state open --json number --jq '.[].number'); do
  gh issue close "$n" --repo tend-agent/tend-integration \
    --comment "Cleaned up by weekly integration-test reset."
done

for n in $(gh pr list --repo tend-agent/tend-integration \
            --state open --json number --jq '.[].number'); do
  gh pr close "$n" --repo tend-agent/tend-integration \
    --delete-branch \
    --comment "Cleaned up by weekly integration-test reset."
done

for b in $(gh api repos/tend-agent/tend-integration/branches \
             --jq '.[].name' | grep -v '^main$' || true); do
  gh api -X DELETE "repos/tend-agent/tend-integration/git/refs/heads/$b"
done
```

## 7. Report failure

If any of §3–§5 failed, open a labeled issue in `max-sixty/tend`. The
label is created on demand so the first failure works without prior
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
<paste the failing command's stderr and any relevant gh run URLs from
tend-agent/tend-integration; do NOT include any secret values>
\`\`\`"
````

Include the test repo's failing workflow run URL in the body when
relevant (capture it during §3/§4 before §6's reset moves on).
