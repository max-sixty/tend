---
name: running-tend
description: Project-specific instructions loaded by tend workflows alongside AGENTS.md.
---

# Running tend — leaf

## Landing

Tend uses `merge: yolo`. **Fix every failure in a red run** defines when a CI
repair can land without maintainer approval.

Changes to workflows, Tend's configuration, CODEOWNERS, or agent instructions
require the control-plane owner's fresh approval.

Merging squashes with `PR_TITLE` / `PR_BODY`, so the description becomes `main`'s
commit message; hold its claims to the standard the diff is held to.

## Fix every failure in a red run

Every failure in the run is the session's, including those earlier runs also hit;
a tracking issue records a failure but doesn't fix it. Open one pull request per
cause that no open pull request already covers. For each durable failure, look
for the pull request that introduced it, first among those merged since the last
run where the failing check passed and then earlier, since a failure that comes
and goes can pass after its cause landed. Establish the cause from the failing
diagnostic and the pull request's context, following **Reading a red suite**.
Where a pull request introduced it, explain which behavior its author intended
and why the repair changes the test or the behavior; link it from the fix's
description.

Merge a pull request that fixes tests, without waiting for maintainer approval,
once each test it claims to fix failed before the change and passes after it
(a skipped or deleted test has not passed). Run `monitor-ci`'s poll on the exact
head to a terminal result. If that result is red only because other tests fail,
merge the verified fix for its subset; handle the other failures separately.
Pull requests run only the nightly tests they edit, so run the claimed ones
yourself. GitHub's applying merge rules still govern the merge.

Merge a fix that is correct but incomplete, and open an issue for what it leaves.
