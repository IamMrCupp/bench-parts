"""The release PR's auto-merge judges each check by its newest run.

release-please opens the release PR and labels it in the same second. CI starts
for both events, and the first run is cancelled in favour of the second. Both
stay in the PR's check rollup, so a green PR carries a row of CANCELLED entries
beside the SUCCESS ones. Counting those as failures left a release sitting
unmerged.

These tests run the workflow's own jq filter — lifted out of the YAML, not a
copy — against rollups shaped like the ones GitHub returns.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release-pr-automerge.yml"

NOT_STARTED = "0001-01-01T00:00:00Z"


def _filter() -> str:
    found = re.search(r"--jq '(.*?)'\)", WORKFLOW.read_text(encoding="utf-8"), re.DOTALL)
    assert found, "the statusCheckRollup filter has moved; point this test at it"
    return found.group(1)


def _not_done(rollup: list[dict]) -> list[str]:
    """What the workflow would list as still running or not passed."""
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq isn't installed")
    out = subprocess.run(
        [jq, "-r", _filter()],
        input=json.dumps({"statusCheckRollup": rollup}),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def _run(
    name: str,
    conclusion: str,
    started: str,
    completed: str,
    *,
    status: str = "COMPLETED",
    workflow: str = "CI",
) -> dict:
    return {
        "__typename": "CheckRun",
        "workflowName": workflow,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "startedAt": f"2026-11-17T05:{started}Z",
        "completedAt": f"2026-11-17T05:{completed}Z" if completed else NOT_STARTED,
    }


def test_a_cancelled_run_replaced_by_a_passing_one_does_not_block() -> None:
    # The rollup that stranded the release: every CI job twice, the first cancelled.
    rollup = [
        _run("lint-test", "CANCELLED", "49:07", "49:07"),
        _run("lint-test", "SUCCESS", "49:11", "49:48"),
        _run("build", "SUCCESS", "49:10", "49:44", workflow="Image"),
        _run("tray-macos", "SKIPPED", "49:07", "49:07"),
        _run("tray-macos", "SKIPPED", "49:08", "49:08"),
        _run("deb-verify", "CANCELLED", "49:08", "49:08"),
        _run("deb-verify", "SUCCESS", "50:00", "50:52"),
    ]
    assert _not_done(rollup) == []


def test_runs_started_in_the_same_second_go_by_which_finished_last() -> None:
    rollup = [
        _run("lint-test", "CANCELLED", "49:07", "49:07"),
        _run("lint-test", "SUCCESS", "49:07", "49:48"),
    ]
    assert _not_done(rollup) == []


def test_a_rerun_that_passes_clears_an_earlier_failure() -> None:
    rollup = [
        _run("lint-test", "FAILURE", "49:07", "49:30"),
        _run("lint-test", "SUCCESS", "52:00", "52:40"),
    ]
    assert _not_done(rollup) == []


def test_the_newest_run_failing_blocks() -> None:
    rollup = [
        _run("lint-test", "SUCCESS", "49:07", "49:30"),
        _run("lint-test", "FAILURE", "52:00", "52:40"),
    ]
    assert _not_done(rollup) == ["lint-test: COMPLETED FAILURE"]


def test_a_check_that_was_only_ever_cancelled_blocks() -> None:
    assert _not_done([_run("lint-test", "CANCELLED", "49:07", "49:07")]) == [
        "lint-test: COMPLETED CANCELLED"
    ]


@pytest.mark.parametrize("status", ["IN_PROGRESS", "QUEUED"])
def test_a_run_still_going_blocks_even_beside_an_older_pass(status: str) -> None:
    rollup = [
        _run("lint-test", "SUCCESS", "49:07", "49:30"),
        _run("lint-test", "", "52:00", "", status=status),
    ]
    assert _not_done(rollup) == [f"lint-test: {status} "]


def test_the_same_job_name_in_another_workflow_is_its_own_check() -> None:
    rollup = [
        _run("build", "FAILURE", "49:07", "49:30", workflow="Image"),
        _run("build", "SUCCESS", "52:00", "52:40", workflow="CI"),
    ]
    assert _not_done(rollup) == ["build: COMPLETED FAILURE"]


def test_a_commit_status_counts_too() -> None:
    def status(state: str) -> dict:
        return {
            "__typename": "StatusContext",
            "context": "external",
            "state": state,
            "startedAt": "2026-11-17T05:49:07Z",
        }

    assert _not_done([status("SUCCESS")]) == []
    assert _not_done([status("PENDING")]) == ["external: COMPLETED PENDING"]


def test_no_checks_at_all_is_not_a_pass() -> None:
    assert _not_done([]) == ["no checks reported yet"]
