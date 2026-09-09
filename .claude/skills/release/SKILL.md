---
name: release
description: Tend release workflow. Use when user asks to "do a release", "release a new version", "cut a release", or wants to publish a new version to PyPI.
metadata:
  internal: true
---

# Release Workflow

## Steps

1. **Record the release target, then sync and validate**: Name the PR or merge commit(s) this release must ship. A bootstrap release made only to restore CI or review does not complete a separate pending change; that change needs its own release after it merges. Fetch and bring the long-lived `release` branch to `origin/main` (`git merge origin/main`, or `git reset --hard origin/main` when it has no commits to preserve), then verify every target with `git merge-base --is-ancestor <commit> HEAD`. Run `wt test` and `uv tool run pre-commit run --all-files`. Record `HEAD` as the cut-from commit for step 9.
2. **Check current version**: Read `version` in `generator/pyproject.toml`
3. **Review commits**: `git log <last-version>..origin/main --oneline` to understand scope — against `origin/main` (not `HEAD`), so the range is the full set of commits this release ships even if step 1 was skipped
4. **Confirm version with user**: Present changes summary and proposed version
5. **Bump version**: Edit `version` in `generator/pyproject.toml`, then `uv lock` at the repo root (the workspace's only lockfile)
6. **Update CHANGELOG**: Add a `## X.Y.Z` section at the top of `CHANGELOG.md` (see "CHANGELOG" below). The release workflow publishes this section verbatim as the GitHub Release notes and **fails the GitHub Release job if the section is missing** (PyPI publish has already happened by then; recovery is a manual `gh release create`), so it must land in the release commit — before the tag.
7. **Commit on the current branch**: `chore: release X.Y.Z` (version bump, lockfile, and CHANGELOG). Don't create a new branch — this worktree is already on the release branch, and the PR opens from it to `main`.
8. **Merge to main**: Push, create PR via `gh pr create`, wait for CI, merge with `gh pr merge --squash`
9. **Verify the changelog covers `main`, then tag and push**: the tag decides what ships — `pypi-release.yaml` publishes to PyPI and builds the GitHub Release from the `## X.Y.Z` section at the tag, so everything reachable from it is in the release. The merge squashes onto whatever `main` tip exists at merge time, so a commit that lands during the PR's CI wait is already an ancestor of the release commit and ships whether or not the changelog mentions it. A direct push to `main` is the easy miss: it never appears in `gh pr list`, so a PR-based cross-check won't find it. List what reached `main` since the cut-from tip (step 1):
    ```bash
    git fetch origin
    git log --oneline <cut-from-commit>..origin/main
    ```
    Clean means the changelog at `origin/main` documents every user-facing commit listed. With no drift the list is one line, the `chore: release X.Y.Z (#NNNN)` squash commit. Fold anything else that's user-facing into the changelog with a follow-up squash PR, then re-fetch and re-run. The list only grows across passes — the drifted commits stay, joined by the follow-up's own squash commit — so each pass re-checks coverage over a longer list.

    Both the tag and the PyPI version are immutable, so this is the last point where a miss is cheap to fix. Once clean, tag `origin/main`, so the check and the tag name the same ref:
    ```bash
    git tag X.Y.Z origin/main && git push origin X.Y.Z
    ```
10. **Wait for publication**: Require the tag workflow to succeed, `uvx tend@X.Y.Z --help` to resolve the exact version, and `gh release view X.Y.Z` to show the release.
11. **Deploy the release to tend**: Stay on the `release` branch. Fetch and `git reset --hard origin/main`, then regenerate with the exact published package: `uvx tend@X.Y.Z init`. Follow `running-tend`'s **Nightly: restamp the hand-maintained workflow refs** in the same commit. Push and open a PR titled `chore: regenerate workflows with tend X.Y.Z`; wait for CI and review, then squash-merge it. Opening the PR is not deployment.
12. **Exercise changed integration surfaces**: If the release changes an action, harness, authentication, model selection, plugin installation, or generated workflow behavior, dispatch a representative workflow for every affected harness from the updated `main`. Inspect the run, not just its conclusion: verify it invokes `@X.Y.Z`, exhibits the intended configuration, and completes a real agent task. The release is complete only after publication, the deployment PR merge, and these required live checks.

If deployment or live validation finds a code defect, fix it on `main` and release the next patch from step 1. When step 11's PR is still open, revert the regeneration commit on `release` and reuse that PR for the patch release; a second PR cannot use the same head branch. The published version remains a completed bootstrap only for the behavior it actually contains; keep any later fix and the original release target open until a tag containing them passes step 12.

## CHANGELOG

`CHANGELOG.md` holds one `## X.Y.Z` section per release, newest first. The header must be exactly `## X.Y.Z` — the release workflow matches it literally to extract the notes.

Draft the section from the commits since the last release (`git log <last-version>..origin/main --oneline`):

- **Group by section**, in order, omitting empty ones: **Improved**, **Fixed**, **Documentation**, **Internal**. Internal is for selected notable internals, not everything.
- **Combine related PRs** into one bullet; cite them all in a trailing `([#a](url), [#b](url))` list. Use full `https://github.com/max-sixty/tend/pull/N` URLs so links resolve from the GitHub Release page.
- **Be brief**: 1–3 sentences per bullet; Internal bullets terser.
- **No editorial framing**: describe what changed, not what was wrong with the old approach.
- **Verify against the diff**, not the commit subject — subjects often undersell or misdescribe. `git show <sha>` anything user-facing before trusting its bullet.

## Version scheme

Tags are bare versions (`0.0.9`), not prefixed (`v0.0.9`).

Generated workflows pin the harness action to the generator's own version
(`max-sixty/tend/claude@X.Y.Z`, `max-sixty/tend/codex@X.Y.Z`); there is no
bare-root action and no floating `v1`. Each `X.Y.Z` tag is the immutable ref
consumers run, enforced by a tag ruleset on `max-sixty/tend`
(`update`/`deletion` restricted). Never force-move or delete a published tag.
Step 9 (tag) must precede step 11 (regenerate via the exact `uvx tend@X.Y.Z`) so the
pinned ref resolves to an existing tag.

## Commit message pattern

```
chore: release X.Y.Z

Bumps generator version to X.Y.Z and syncs lockfile.

N commits since A.B.C: <brief list of notable changes with PR numbers>.
```
