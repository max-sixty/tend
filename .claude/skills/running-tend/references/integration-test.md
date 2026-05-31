# Weekly integration test

Drives a real issue and a real PR against a persistent test repo, asserts
`tend-triage` and `tend-review` ran end-to-end, and resets the repo for
the next week.

## Safety — read first

This recipe issues destructive operations (close issues, close PRs,
delete branches, overwrite secrets) against **exactly one** repo:
`tend-agent/tend-integration`. The literal string `tend-agent/tend-integration`
appears as `--repo` argument on every destructive call below — not a
variable. **Do not substitute a variable**, do not rename the repo, do
not run this recipe against any other repo.

If `tend-agent/tend-integration` does not exist yet, the §1 bootstrap
creates it. Once it exists, subsequent weekly runs only operate on it.

`$GITHUB_TOKEN` (bot PAT) is present in the agent's env, set on the
claude step in `action.yaml`. `gh` authenticates as `tend-agent` via
`$GITHUB_TOKEN` automatically.

`$CLAUDE_CODE_OAUTH_TOKEN` is **not** available here — the harness sets
`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`, which strips Anthropic credentials
from this subprocess. Rotation on the integration repo is therefore a
manual maintainer task; the reseed below is guarded so a scrubbed env
never clobbers the stored secret.

The bot PAT needs `workflow` (to push generated workflow files) but
**does not** need `delete_repo` — the recipe never deletes the test
repo; it resets in place.

Run steps in order. If §3, §4, or §5 fails, jump to §6 (reset), then §7
(report).

## 1. Bootstrap (first run only — idempotent on subsequent runs)

Create the test repo if missing, with workflows installed on `main`,
branch protection enabled on the default branch, and both secrets set.
This block is a no-op once the repo exists.

```bash
if ! gh repo view tend-agent/tend-integration --json name >/dev/null 2>&1; then
  gh repo create tend-agent/tend-integration --public --add-readme

  WORK=$(mktemp -d)
  gh repo clone tend-agent/tend-integration "$WORK"
  cd "$WORK"

  # The runner has no global git identity; commit needs both fields set
  # locally or `git commit` aborts and the follow-up push silently no-ops.
  git config user.email "tend-agent@users.noreply.github.com"
  git config user.name "tend-agent"

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

  # tend's preflight requires the default branch to be protected
  # (`gh api .../branches/main --jq '.protected'` must be true). Without
  # this, every tend-* run on the repo aborts at the Security preflight
  # step. The bot owns this repo so it has admin to set protection.
  gh api -X PUT repos/tend-agent/tend-integration/branches/main/protection \
    -H "Accept: application/vnd.github+json" \
    --input - <<'EOF'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
fi

# Reseed TEND_BOT_TOKEN every run (env var is present and rotates with
# the parent workflow's secret).
printf '%s' "$GITHUB_TOKEN" \
  | gh secret set TEND_BOT_TOKEN --repo tend-agent/tend-integration

# CLAUDE_CODE_OAUTH_TOKEN is scrubbed from this subprocess by the
# harness — an unguarded reseed would pipe empty into the secret and
# break every subsequent tend-* run on the integration repo at the
# action's auth preflight. Only reseed if the env var actually has a
# value (it currently never will under env-scrub; the guard exists so
# the recipe is safe if that ever changes).
if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
  printf '%s' "$CLAUDE_CODE_OAUTH_TOKEN" \
    | gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo tend-agent/tend-integration
fi
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
`tend-review` to register and finish, assert the action invoked the
Claude session (artifact present).

The `tend-review` skill is explicitly directed to exit silently on
self-authored, trivial PRs (GitHub blocks self-approval; the skill keeps
quiet when there are no concerns). So an "is there a bot review on the
PR?" assertion can't distinguish "the action never ran" from "the action
ran and stayed silent by design" — both produce zero reviews. Asserting
on the session-log artifact, which `claude-code-action` uploads
unconditionally on every invocation, distinguishes the two.

```bash
WORK=$(mktemp -d)
gh repo clone tend-agent/tend-integration "$WORK"
cd "$WORK"

git config user.email "tend-agent@users.noreply.github.com"
git config user.name "tend-agent"

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

# Session-log artifact presence proves claude-code-action invoked the
# Claude session. The skill may then post a review, post nothing, or
# anything in between — that's a separate concern from "did tend-review
# fire end-to-end?".
ARTIFACTS=$(gh api "repos/tend-agent/tend-integration/actions/runs/$RUN_ID/artifacts" \
  --jq '[.artifacts[] | select(.name | startswith("claude-session-logs"))] | length')
[ "$ARTIFACTS" -ge 1 ] || { echo "tend-review: no session-log artifact on run $RUN_ID"; exit 1; }

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

Assemble the body via a quoted heredoc (so bash doesn't try to evaluate
the inner backticks) and substitute the run URL through `envsubst`:

````bash
gh label create integration-test-failure --color B60205 \
  --repo max-sixty/tend 2>/dev/null || true

export RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
envsubst '$RUN_URL' > /tmp/integration-failure.md <<'EOF'
Run: $RUN_URL

Failed at <step>. Captured output:

```
<paste the failing command's stderr and any relevant gh run URLs from
tend-agent/tend-integration; do NOT include any secret values>
```
EOF

gh issue create --repo max-sixty/tend \
  --title "Weekly integration test failed" \
  --label integration-test-failure \
  --body-file /tmp/integration-failure.md
````

Include the test repo's failing workflow run URL in the body when
relevant (capture it during §3/§4 before §6's reset moves on).
