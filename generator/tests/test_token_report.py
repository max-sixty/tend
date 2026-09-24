"""Tests for plugins/tend-ci-runner/scripts/token_report.py.

The report exists to answer "what did the spend go to?", so what is pinned
here is the grouping and the ranking: subjects come off the record each run
uploaded, and cost — not the token count — leads and orders every table.
Cache reads are ~97% of the tokens and ~60% of the bill, so a table ranked by
summed tokens ranks the cheapest work first; the fixtures below make the two
orders disagree so a regression to token-ranking fails.

The fake `gh` serves a run list and materialises each run's artifact into the
`--dir` the script passes, which is the whole of what the script needs from
GitHub. Every case anchors the window with `--since` rather than `--hours`, so
the fixtures' timestamps stay fixed instead of ageing out of a wall-clock
window.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests import GH_PREAMBLE, fake_bin, tool_path, uv_script

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "plugins" / "tend-ci-runner" / "scripts" / "token_report.py"
# Before every fixture timestamp below, so a case that isn't about the window
# sees all of its runs.
ANCHOR = "2026-08-01T00:00:00Z"

FAKE_GH = (
    GH_PREAMBLE
    + r"""case "$1 $2" in
  "workflow list")
    [ -n "${WORKFLOW_LIST_FAILS:-}" ] && { echo "workflows unavailable" >&2; exit 42; }
    emit "$(cat "$WF_JSON")"
    ;;
  "run list")
    # `--created >=X` is a server-side filter, so the fake honours it: the
    # script's cushion is only real if a row outside it never arrives.
    wf=""
    created=""
    prev=""
    for a in "$@"; do
      [ "$prev" = "--workflow" ] && wf="$a"
      [ "$prev" = "--created" ] && created="${a#>=}"
      prev="$a"
    done
    emit "$(jq -c --arg wf "$wf" --arg created "$created" \
      '[.[] | select(.name == $wf) | select($created == "" or .createdAt >= $created)]' \
      "$RUNS_JSON")"
    ;;
  "run download")
    # The artifact the run uploaded, or a non-zero exit for a run that has
    # none — the script skips those.
    dir=""
    prev=""
    for a in "$@"; do
      [ "$prev" = "--dir" ] && dir="$a"
      prev="$a"
    done
    [ -f "$USAGE_DIR/$3.json" ] || exit 1
    harness="$(cat "$USAGE_DIR/$3.harness")"
    mkdir -p "$dir/$harness-session-logs-1"
    cp "$USAGE_DIR/$3.json" "$dir/$harness-session-logs-1/token-usage.json"
    ;;
  *)
    exit 1
    ;;
esac
"""
)


def _record(**over: Any) -> dict[str, Any]:
    """One job's token-usage.json, as the "Token usage" step writes it."""
    return {
        "repo": "owner/repo",
        "workflow": "tend-review",
        "run_id": 1,
        "run_attempt": 1,
        "event": "pull_request_target",
        "number": 851,
        "head_sha": "head0000",
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 10000,
        "turns": 3,
        "model": "opus",
        "cost_usd": 1.0,
        "partial": False,
        **over,
    }


class Report:
    """A configured run of the script: add runs, then read the two outputs."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp = tmp_path
        self._runs: list[dict[str, Any]] = []
        self._usage_dir = tmp_path / "usage"
        self._usage_dir.mkdir()
        self._env = {
            "PATH": tool_path(fake_bin(tmp_path, gh=FAKE_GH)),
            "GH_CALLS": str(tmp_path / "gh-calls.log"),
            "WF_JSON": str(tmp_path / "wf.json"),
            "RUNS_JSON": str(tmp_path / "runs.json"),
            "USAGE_DIR": str(self._usage_dir),
        }

    def add(
        self,
        run_id: int,
        *,
        workflow: str = "tend-review",
        harness: str = "claude",
        created_at: str | None = None,
        updated_at: str | None = None,
        **over: Any,
    ) -> Report:
        """A completed run and the artifact it uploaded."""
        created_at = created_at or f"2026-08-2{run_id % 10}T12:00:00Z"
        self._runs.append(
            {
                "databaseId": run_id,
                "conclusion": "success",
                "createdAt": created_at,
                "updatedAt": updated_at or created_at,
                "name": workflow,
            }
        )
        (self._usage_dir / f"{run_id}.json").write_text(
            json.dumps(_record(run_id=run_id, workflow=workflow, **over))
        )
        (self._usage_dir / f"{run_id}.harness").write_text(harness)
        return self

    def add_raw(self, run_id: int, body: str) -> Report:
        """A run whose artifact holds *body* verbatim, valid JSON or not."""
        self.add_run_without_artifact(run_id)
        (self._usage_dir / f"{run_id}.json").write_text(body)
        (self._usage_dir / f"{run_id}.harness").write_text("claude")
        return self

    def add_run_without_artifact(self, run_id: int) -> Report:
        self._runs.append(
            {
                "databaseId": run_id,
                "conclusion": "cancelled",
                "createdAt": "2026-08-25T12:00:00Z",
                "updatedAt": "2026-08-25T12:00:00Z",
                "name": "tend-review",
            }
        )
        return self

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run the script with *args* as its whole command line."""
        workflows = sorted({run["name"] for run in self._runs})
        Path(self._env["WF_JSON"]).write_text(
            json.dumps([{"name": name} for name in workflows])
        )
        Path(self._env["RUNS_JSON"]).write_text(json.dumps(self._runs))
        return subprocess.run(
            uv_script(SCRIPT, *args),
            env=self._env,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> str:
        return Path(self._env["GH_CALLS"]).read_text()

    def run(self, *prefixes: str) -> tuple[dict[str, Any], list[list[str]]]:
        """The JSON on stdout, and the stderr summary split into cells.

        The summary's blocks — the prose header, each table, the footnotes —
        are separated by blank lines, and ``blocks`` keeps that structure so a
        test can address one table without a footnote sentence running into it.
        """
        return self.run_args("--since", ANCHOR, *prefixes)

    def run_args(self, *args: str) -> tuple[dict[str, Any], list[list[str]]]:
        """As `run`, with the whole command line spelled out."""
        result = self.invoke(*args)
        assert result.returncode == 0, result.stderr
        self.stderr = result.stderr
        self.blocks = [
            cells
            for block in result.stderr.split("\n\n")
            if (cells := [line.split() for line in block.splitlines() if line.strip()])
        ]
        rows = [line.split() for line in result.stderr.splitlines() if line.strip()]
        return json.loads(result.stdout), rows


@pytest.fixture
def report(tmp_path: Path) -> Report:
    return Report(tmp_path)


def _table(report: Report, header: str) -> list[list[str]]:
    """The rows of the table whose first heading is *header*, without it."""
    return next(block for block in report.blocks if block[0][0] == header)[1:]


def test_the_record_carries_the_subject_so_no_join_is_needed(report: Report) -> None:
    """Each run's PR, event and commit ride on its own artifact.

    Without them every question about what the spend went to costs a second
    query per run; the run list the script already makes cannot answer them —
    its `headSha` for a `pull_request_target` run is the base branch, not the
    commit the workflow checked out.
    """
    report.add(1).add(2, number=852, head_sha="head0002")
    output, _ = report.run()

    runs = {run["run_id"]: run for run in output["runs"]}
    assert runs[1]["number"] == 851
    assert runs[1]["subject"] == "#851"
    assert runs[1]["event"] == "pull_request_target"
    assert runs[1]["repo"] == "owner/repo"
    assert runs[1]["head_sha"] == "head0000"
    assert runs[2]["subject"] == "#852"


def test_repeat_runs_on_one_subject_collapse_into_one_row(report: Report) -> None:
    """The costly pattern is many runs on one PR, which is one row with a count.

    Three reviews of #851 and one of #852: the subject table has to show two
    rows, the repeat one carrying its run count and its summed cost.
    """
    for run_id in (1, 2, 3):
        report.add(run_id, cost_usd=2.0)
    report.add(4, number=852, cost_usd=1.0)
    report.run()

    assert _table(report, "SUBJECT") == [
        ["#851", "3", "$6.00", "tend-review", "30K"],
        ["#852", "1", "$1.00", "tend-review", "10K"],
    ]


def test_equal_timestamps_keep_discovery_order(report: Report) -> None:
    created_at = "2026-08-25T12:00:00Z"
    report.add(1, created_at=created_at).add(2, created_at=created_at)

    output, _ = report.run()

    assert [run["run_id"] for run in output["runs"]] == [1, 2]


def test_tables_are_ranked_by_cost_not_by_token_count(report: Report) -> None:
    """Cache reads dominate the count and not the bill, so ranking by tokens
    ranks the cheapest work first.

    `tend-nightly` here reads ten times the cache for a tenth of the cost —
    the shape of a real fleet's spend, and the two orders are opposite.
    """
    report.add(1, workflow="tend-nightly", cost_usd=0.5, cache_read_input_tokens=999999)
    report.add(2, workflow="tend-review", cost_usd=9.0, cache_read_input_tokens=1000)
    report.run()

    workflows = [row[0] for row in _table(report, "WORKFLOW")]
    assert workflows == [
        "tend-review",
        "tend-nightly",
    ], "the costliest workflow leads; ranking by cache reads inverts this"
    assert _table(report, "WORKFLOW")[0][2] == "$9.00", "cost is the second column"


def test_a_cost_unknown_run_reads_as_a_floor_not_as_free(report: Report) -> None:
    """A cancelled run's tokens are real and its cost is unrecoverable.

    Cost now leads every table, so a reconstructed run must never read as
    cheap: its cells carry `+`, and the total names how many runs it covers.
    """
    report.add(1, cost_usd=None, partial=True)
    report.add(2, number=852, cost_usd=4.0)
    output, rows = report.run()

    assert output["totals"]["partial_runs"] == 1
    total = next(row for row in rows if row[0] == "Total")
    assert total[2] == "$4.00+", "the headline cost is a floor while a run is unpriced"
    assert " ".join(total).endswith("(1 of 2 runs cost-unknown)")
    assert _table(report, "SUBJECT")[1] == ["#851", "1", "n/a", "tend-review", "10K"]


def test_a_run_whose_record_predates_the_subject_fields(report: Report) -> None:
    """An artifact still in the window from before this change still reports.

    Its counts are all it has, so it groups under `?` rather than dropping out
    of the totals.
    """
    report.add(1, number=None, head_sha=None, repo=None, event=None)
    output, _ = report.run()

    assert output["runs"][0]["subject"] == "?"
    assert output["totals"]["cost_usd"] == 1.0
    assert _table(report, "SUBJECT") == [["?", "1", "$1.00", "tend-review", "10K"]]


def test_a_run_with_no_artifact_is_skipped(report: Report) -> None:
    """A run killed before upload contributes no row rather than a zero one."""
    report.add(1).add_run_without_artifact(2)
    output, _ = report.run()

    assert [run["run_id"] for run in output["runs"]] == [1]


def test_codex_artifact_counts_tokens_without_claiming_zero_cost(
    report: Report,
) -> None:
    report.add(
        1,
        harness="codex",
        model="gpt-6-sol",
        cost_usd=0,
        input_tokens=4200,
        cache_read_input_tokens=0,
        cached_input_tokens=3000,
    )
    report.add(2, workflow="tend-nightly", cost_usd=2.5)
    output, rows = report.run()

    assert "--pattern *-session-logs*" in report.calls()
    assert output["totals"]["input_tokens"] == 4210
    assert output["totals"]["cache_read_input_tokens"] == 13000
    assert output["totals"]["unpriced_runs"] == 1
    assert output["runs"][1]["cost_usd"] is None
    assert _table(report, "WORKFLOW")[1][2] == "n/a"
    assert _table(report, "COST-UNKNOWN")[0][0] == "#851"
    total = next(row for row in rows if row[0] == "Total")
    assert total[2] == "$2.50+"


def test_codex_only_report_has_no_cost_value(report: Report) -> None:
    report.add(1, harness="codex", model="gpt-6-sol", cost_usd=0)
    output, rows = report.run()

    assert output["runs"][0]["cost_usd"] is None
    assert next(row for row in rows if row[0] == "Total")[2] == "n/a"


def test_the_subject_table_stops_at_the_top_and_says_so(report: Report) -> None:
    """Past the top the tail is one-run subjects; the JSON on stdout has them.

    The cap and the note it prints are the same value, so a reader is never
    told a count the table contradicts.
    """
    for run_id in range(1, 26):
        report.add(run_id, number=1000 + run_id, cost_usd=float(run_id))
    output, rows = report.run()

    subjects = _table(report, "SUBJECT")
    assert len(subjects) == 20
    assert subjects[0][0] == "#1025", "the costliest subject leads"
    assert len(output["runs"]) == 25
    assert any("costliest of 25" in " ".join(row) for row in rows)


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ('{"input_tokens": 5,', "cut mid-write"),
        ("   \n", "holding nothing but whitespace"),
    ],
)
def test_one_unreadable_artifact_costs_its_own_run_and_no_other(
    report: Report, body: str, why: str
) -> None:
    """An artifact cut mid-write must not take the other 200 runs with it.

    `jq` exits non-zero on it, and under `set -euo pipefail` that ends the
    script before anything reaches stdout — every other run's spend lost to one
    bad file. A file holding no object at all takes the same path: `jq` exits
    0 with no output, and reporting the run as a zero would say its spend was
    nothing when the truth is that it is unknown.
    """
    report.add(1, cost_usd=3.0).add_raw(2, body).add(3, number=852)
    output, rows = report.run()

    assert [run["run_id"] for run in output["runs"]] == [3, 1], f"lost a run to {why}"
    assert output["totals"]["cost_usd"] == 4.0
    assert output["totals"]["skipped_runs"] == 1
    assert any("1 run(s) uploaded no readable" in " ".join(row) for row in rows)


def test_cost_unknown_runs_get_their_own_ranked_table(report: Report) -> None:
    """A cancelled run is booked at $0, so the cost sort buries it.

    A subject whose runs were all cancelled would otherwise be cut from a
    report about where the tokens went — here it holds most of the fleet's
    cache reads and none of its priced cost.
    """
    for run_id in range(1, 22):
        report.add(run_id, number=1000 + run_id, cost_usd=float(run_id))
    for run_id in range(90, 99):
        report.add(
            run_id,
            number=2222,
            cost_usd=None,
            partial=True,
            cache_read_input_tokens=5_000_000,
        )
    report.run()

    assert "#2222" not in [row[0] for row in _table(report, "SUBJECT")], (
        "a $0 floor cannot outrank priced work, which is why it needs its own table"
    )
    assert _table(report, "COST-UNKNOWN") == [
        ["#2222", "9", "45M", "900", "tend-review"]
    ]


def test_a_matrix_runs_row_agrees_with_its_rollup_to_the_cent(report: Report) -> None:
    """Per-job costs are exact to the cent; their float sum is not.

    Truncating in the row and rounding in the rollup made one run's own row
    read a cent below the workflow total it was the whole of.
    """
    report.add(1, cost_usd=5.13)
    for cost in (8.57, 5.31, 8.12, 1.04):
        (report._usage_dir / "1.json").write_text(
            (report._usage_dir / "1.json").read_text().rstrip()
            + "\n"
            + json.dumps(_record(run_id=1, cost_usd=cost))
        )
    output, _ = report.run()

    assert output["totals"]["cost_usd"] == 28.17
    assert _table(report, "RUN")[0][3] == "$28.17"
    assert _table(report, "WORKFLOW")[0][2] == "$28.17"


def test_half_cent_costs_round_up_consistently(report: Report) -> None:
    report.add(1, cost_usd=1.005)

    output, _ = report.run()

    assert output["totals"]["cost_usd"] == 1.01
    assert _table(report, "RUN")[0][3] == "$1.01"


def test_a_workflow_name_with_a_space_keeps_its_own_column(report: Report) -> None:
    """The summary pads its own columns, so a cell may contain spaces.

    `EXTRA_PREFIXES` exists for hand-written workflows, whose names are not
    held to the generator's `tend-` convention — and a shifted column reads as
    a different metric rather than as a broken table.
    """
    report.add(1, workflow="review reviewers")
    report.run("review")

    lines = report.stderr.splitlines()
    header = next(line for line in lines if line.startswith("WORKFLOW"))
    row = lines[lines.index(header) + 1]
    assert row[header.index("RUNS") :].split()[0] == "1", (
        "the run count must sit under RUNS; a summary that split rows on "
        "whitespace would put `reviewers` there and shift every column right"
    )


@pytest.mark.parametrize("prefix", ['review "', "review \\"])
def test_workflow_prefix_is_passed_to_jq_as_data(report: Report, prefix: str) -> None:
    report.add(1, workflow=f"{prefix}special")

    output, _ = report.run(prefix)

    assert [run["run_id"] for run in output["runs"]] == [1]


def test_a_failed_workflow_listing_aborts_instead_of_reporting_zero(
    report: Report,
) -> None:
    report._env["WORKFLOW_LIST_FAILS"] = "1"

    result = report.invoke()

    assert result.returncode != 0
    assert result.stdout == ""


def test_runs_with_no_artifact_are_counted_not_dropped(report: Report) -> None:
    """A codex-harness repo would otherwise read a report of zero runs."""
    report.add(1).add_run_without_artifact(2).add_run_without_artifact(3)
    _, rows = report.run()

    assert any("2 run(s) uploaded no" in " ".join(row) for row in rows)


def test_a_repo_with_no_runs_reports_the_same_empty_shape(report: Report) -> None:
    """The empty report comes out of the ordinary path, not a special case.

    It used to be a JSON literal echoed from two early exits, which is one
    place for the totals shape to drift out of sync with the code that builds
    it — and it had already lost `skipped_runs`.
    """
    output, _ = report.run()

    assert output == {
        "runs": [],
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "turns": 0,
            "cost_usd": 0,
            "partial_runs": 0,
            "unpriced_runs": 0,
            "skipped_runs": 0,
        },
    }


def test_a_run_that_straddles_the_anchor_is_priced(report: Report) -> None:
    """The census admits on completion, so the spend has to as well.

    A nightly that began before the anchor and finished inside the window is
    censused by `list_recent_runs.py`; a creation-filtered fetch drops it, and
    the runs it drops are by construction the longest and costliest ones.
    """
    report.add(
        1,
        workflow="tend-nightly",
        created_at="2026-09-17T06:39:00Z",
        updated_at="2026-09-17T08:17:00Z",
        cost_usd=21.08,
    )

    output, _ = report.run_args("--since", "2026-09-17T07:00:00Z")

    assert [run["run_id"] for run in output["runs"]] == [1]
    assert output["totals"]["cost_usd"] == 21.08


def test_a_run_that_finished_before_the_anchor_stays_out(report: Report) -> None:
    """The cushion widens the fetch, not the window.

    The previous sweep already priced these, so admitting them would
    double-count the spend the series is read window-over-window.
    """
    report.add(1, created_at="2026-09-16T12:00:00Z", updated_at="2026-09-16T12:30:00Z")
    report.add(2, created_at="2026-09-17T06:39:00Z", updated_at="2026-09-17T08:17:00Z")

    output, _ = report.run_args("--since", "2026-09-17T07:00:00Z")

    assert [run["run_id"] for run in output["runs"]] == [2]


def test_the_fetch_reaches_a_day_behind_the_anchor(report: Report) -> None:
    """`CREATION_CUSHION` here matches the census's, so the two see one band."""
    report.add(1, created_at="2026-09-17T08:00:00Z", updated_at="2026-09-17T08:10:00Z")

    report.run_args("--since", "2026-09-17T07:00:00Z")

    assert "--created >=2026-09-16T07:00:00Z" in report.calls()


def test_the_header_names_the_anchor_not_the_cushioned_fetch(report: Report) -> None:
    report.add(1, created_at="2026-09-17T08:00:00Z", updated_at="2026-09-17T08:10:00Z")

    _, rows = report.run_args("--since", "2026-09-17T07:00:00Z")

    assert any("since 2026-09-17T07:00:00Z" in " ".join(row) for row in rows)


def test_an_hour_count_in_the_prefix_slot_is_an_error(report: Report) -> None:
    """The window used to be the first positional, so a stale call site would
    otherwise read `24` as a workflow prefix and report an empty fleet."""
    result = report.invoke("24", "review-")

    assert result.returncode == 2
    assert "--hours 24" in result.stderr
    assert result.stdout == ""


def test_an_empty_since_is_an_error_not_a_week_long_window(report: Report) -> None:
    """`--since "$(cat ...)"` collapses to `--since ""` when the anchor is absent.

    Falling through to the `--hours` default would publish a week's spend under
    a header naming a window nobody asked for, with a zero exit.
    """
    report.add(1, created_at="2026-09-17T08:00:00Z", updated_at="2026-09-17T08:10:00Z")

    result = report.invoke("--since", "")

    assert result.returncode == 2
    assert "--since" in result.stderr
    assert result.stdout == ""
