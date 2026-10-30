"""Command boundaries for the deterministic CI helpers shipped with Tend."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from tend.runtime.ci import (
    active_subject_runs,
    bot_review_state,
    check_body_links,
    enrich_tend_outage_issues,
    land_pr,
    list_recent_runs,
    nightly_survey_files,
    nightly_workflow_update,
    pat_scope_audit,
    poll_pr_checks,
    red_default_branch_runs,
    rerun_failed_jobs,
    review_preflight,
    review_reviewers,
    review_runs,
    review_runs_corrections,
    token_report,
)
from tend.runtime.shared import _common


class CommitOID(click.ParamType):
    name = "commit OID"

    def convert(self, value, param, ctx):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            self.fail(
                f"{value!r} must be a full 40-char lowercase commit OID — UNVERIFIED, not green",
                param,
                ctx,
            )
        return value


OID = CommitOID()


# Preserve string IDs at the API boundary without accepting signs or fractions.
class NumericID(click.ParamType):
    name = "numeric ID"

    def convert(self, value, param, ctx):
        if not str(value).isdigit():
            self.fail("must be a numeric ID", param, ctx)
        return str(value)


ID = NumericID()


def _run(operation: Callable[[], int | None]) -> None:
    try:
        code = operation()
    except subprocess.CalledProcessError as error:
        code = error.returncode or 1
    click.get_current_context().exit(code or 0)


def _run_evidence(operation: Callable[[], int | None]) -> None:
    try:
        _run(operation)
    except (KeyError, OSError, ValueError) as error:
        click.echo(str(error), err=True)
        click.get_current_context().exit(2)


@click.group("land-pr")
def land() -> None:
    """Record a reviewed head for landing and inspect completed checks."""


@land.command("pushed")
@click.argument("sha", type=OID)
def pushed(sha: str) -> None:
    _run(lambda: land_pr.pushed(sha))


@land.command("intend")
@click.argument("pr", type=ID)
@click.argument("sha", type=OID)
@click.argument("base")
@click.option("--skip", multiple=True)
def intend(pr: str, sha: str, base: str, skip: tuple[str, ...]) -> None:
    _run(lambda: land_pr.intend(pr, sha, base, skip=frozenset(skip)))


@land.command("check")
@click.argument("sha", type=OID)
def check(sha: str) -> None:
    _run(lambda: land_pr.check(sha))


@click.group("bot-review-state")
def review_state() -> None:
    """Read and update the bot's reviews and approval state."""


@review_state.command("state")
@click.argument("pr", type=ID)
def state(pr: str) -> None:
    _run(lambda: _common.dump(bot_review_state.fetch_review_state(pr)))


@review_state.command("feedback")
@click.argument("pr", type=ID)
def feedback(pr: str) -> None:
    _run(lambda: _common.dump(bot_review_state.feedback(pr)))


@review_state.command("prepare-approval")
@click.argument("pr", type=ID)
def prepare_approval(pr: str) -> None:
    def prepare() -> None:
        bot_review_state.prepare_approval(pr)

    _run(prepare)


@review_state.command("dismiss")
@click.argument("pr", type=ID)
@click.argument("message")
def dismiss(pr: str, message: str) -> None:
    _run(lambda: bot_review_state.dismiss_standing_approval(pr, message))


@review_state.command("dismiss-stale")
@click.argument("pr", type=ID)
@click.argument("message")
def dismiss_stale(pr: str, message: str) -> None:
    _run(lambda: bot_review_state.dismiss_stale_approval(pr, message))


@review_state.command("resolve-thread")
@click.argument("thread_id")
def resolve_thread(thread_id: str) -> None:
    _run(lambda: bot_review_state.resolve_thread(thread_id))


@click.group("review-preflight")
def preflight() -> None:
    """Bind a review post to the open PR head actually reviewed."""


@preflight.command("start")
@click.argument("pr", type=ID)
def start_review(pr: str) -> None:
    _run(lambda: review_preflight.start(pr))


@preflight.command("post")
@click.argument("pr", type=ID)
@click.option("--edit-review", type=ID)
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def post_review(pr: str, edit_review: str | None, command: tuple[str, ...]) -> None:
    _run(lambda: review_preflight.post(pr, edit_review=edit_review, command=command))


@click.command("poll-pr-checks")
@click.argument("operation", type=click.Choice(["poll", "approval"]))
@click.argument("pr", type=ID)
@click.argument("sha", type=OID)
@click.option("--no-wait", is_flag=True)
@click.option("--skip", multiple=True)
def poll_checks(
    operation: str, pr: str, sha: str, no_wait: bool, skip: tuple[str, ...]
) -> None:
    """Wait for or inspect a commit's checks and print its final verdict."""
    _run(
        lambda: poll_pr_checks.main(
            operation, pr, sha, wait=not no_wait, skip=frozenset(skip)
        )
    )


@click.command("rerun-failed-jobs")
@click.argument("run_id", type=ID)
def rerun_jobs(run_id: str) -> None:
    """Rerun failed jobs and wait for that new attempt."""
    _run(lambda: rerun_failed_jobs.main(run_id))


@click.command("active-subject-runs")
@click.argument("subject_url")
def active_runs(subject_url: str) -> None:
    """Find active dedicated runs for a notification's subject."""
    _run(lambda: active_subject_runs.main(subject_url))


@click.command("check-body-links")
@click.argument(
    "body_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def check_links(body_path: Path) -> None:
    """Check repository links in a proposed GitHub post."""
    _run(lambda: check_body_links.main(body_path))


@click.command("list-recent-runs")
@click.argument("profile", type=click.Choice(["review-reviewers", "review-runs"]))
@click.argument("prefixes", nargs=-1)
def list_runs(profile: str, prefixes: tuple[str, ...]) -> None:
    """List the runs since the previous successful review sweep."""
    _run(lambda: list_recent_runs.main(profile, prefixes))


@click.command("nightly-survey-files")
def survey_files() -> None:
    """List the tracked files in today's survey bucket."""
    _run(nightly_survey_files.main)


@click.group("nightly-workflow-update")
def workflow_update() -> None:
    """Prepare and publish the nightly generated-workflow update."""


@workflow_update.command("prepare")
def prepare_workflows() -> None:
    _run(nightly_workflow_update.prepare)


@workflow_update.command("ship")
def ship_workflows() -> None:
    _run(nightly_workflow_update.ship)


@workflow_update.command("version")
def workflow_version() -> None:
    print(nightly_workflow_update.version(Path.cwd()))


@click.command("pat-scope-audit")
def scope_audit() -> None:
    """Report the bot token's granted and missing scopes."""
    _run(pat_scope_audit.main)


@click.command("red-default-branch-runs")
def red_runs() -> None:
    """List unresolved failed runs on the repository's default branch."""
    _run(red_default_branch_runs.main)


@click.command("enrich-tend-outage-issues")
def enrich_outages() -> None:
    """Append pending run diagnostics to open Tend outage trackers."""
    _run(enrich_tend_outage_issues.main)


@click.command("review-runs-corrections")
@click.argument("since")
def corrections(since: str) -> None:
    """Collect maintainer corrections since an RFC3339 timestamp."""
    _run(lambda: review_runs_corrections.main(since))


@click.group("review-runs")
def run_evidence() -> None:
    """Maintain the run-review evidence store."""


@run_evidence.command("prepare-evidence")
def prepare_run_evidence() -> None:
    _run_evidence(review_runs.prepare)


@run_evidence.command("append-evidence")
def append_run_evidence() -> None:
    _run_evidence(review_runs.append)


@run_evidence.command("outage-trackers")
def outage_trackers() -> None:
    _run_evidence(review_runs.outage_trackers)


@click.group("review-reviewers")
def reviewer_evidence() -> None:
    """Maintain the review-reviewers evidence store."""


@reviewer_evidence.command("prepare-evidence")
@click.argument("target")
def prepare_reviewer_evidence(target: str) -> None:
    if "/" not in target:
        raise click.BadParameter("expected owner/repo", param_hint="TARGET")
    _run_evidence(lambda: review_reviewers.prepare(target))


@reviewer_evidence.command("append-evidence")
def append_reviewer_evidence() -> None:
    _run_evidence(review_reviewers.append)


@click.command("token-report")
@click.option("--since", help="Price runs completed at or after this ISO timestamp.")
@click.option("--hours", type=int, help="Price the last N hours (default 168).")
@click.argument("prefixes", nargs=-1)
def tokens(since: str | None, hours: int | None, prefixes: tuple[str, ...]) -> None:
    """Summarize token usage for Tend and additional workflow prefixes."""
    if since is not None and hours is not None:
        raise click.UsageError("--since and --hours are mutually exclusive")
    if numeric := next((p for p in prefixes if p.isdigit()), None):
        raise click.UsageError(
            f"PREFIX {numeric!r} is an hour count — pass --hours {numeric}"
        )
    if since is not None:
        try:
            completed_after = token_report._parse_time(since.strip())
        except ValueError as error:
            raise click.BadParameter(
                f"not an ISO timestamp: {since!r}", param_hint="--since"
            ) from error
    else:
        completed_after = datetime.now(UTC) - timedelta(
            hours=168 if hours is None else hours
        )
    _run(lambda: token_report.main(completed_after, prefixes))


def register(runtime: click.Group) -> None:
    """Expose CI commands on the single installed runtime entry point."""
    for command in (
        land,
        review_state,
        preflight,
        poll_checks,
        rerun_jobs,
        active_runs,
        check_links,
        list_runs,
        survey_files,
        workflow_update,
        scope_audit,
        red_runs,
        enrich_outages,
        corrections,
        run_evidence,
        reviewer_evidence,
        tokens,
    ):
        runtime.add_command(command)
