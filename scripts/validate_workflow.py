#!/usr/bin/env python
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from quran_video.automation.schedule import AUTOMATION_SCHEDULE_CRON, should_run_for_schedule


def main() -> int:
    workflow = Path(".github/workflows/daily-quran-video.yml")
    if not workflow.exists():
        print("daily workflow is missing", file=sys.stderr)
        return 1
    payload = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    schedules = [item["cron"] for item in payload[True]["schedule"]]
    expected = {AUTOMATION_SCHEDULE_CRON}
    if set(schedules) != expected:
        print(f"unexpected schedule entries: {schedules}", file=sys.stderr)
        return 1
    checkout = payload["jobs"]["render-and-upload"]["steps"][0]
    expected_ref = (
        "${{ github.event_name == 'schedule' && "
        "github.event.repository.default_branch || github.ref }}"
    )
    if checkout.get("with", {}).get("ref") != expected_ref:
        print("scheduled checkout does not refresh the default branch", file=sys.stderr)
        return 1
    dispatch_inputs = payload[True]["workflow_dispatch"]["inputs"]
    if "auth_check_only" not in dispatch_inputs:
        print("workflow is missing the YouTube authorization-only check", file=sys.stderr)
        return 1
    render_step = next(
        step
        for step in payload["jobs"]["render-and-upload"]["steps"]
        if step.get("name") == "Render automation candidate"
    )
    if render_step.get("env", {}).get("AUTH_CHECK_ONLY") != (
        "${{ inputs.auth_check_only || 'false' }}"
    ):
        print("workflow does not route the authorization-only input", file=sys.stderr)
        return 1
    if not should_run_for_schedule(
        "schedule", AUTOMATION_SCHEDULE_CRON, datetime(2026, 1, 1, 0, 37, tzinfo=UTC)
    ):
        print("scheduled workflow gate failed", file=sys.stderr)
        return 1
    if should_run_for_schedule("schedule", "0 * * * *", datetime(2026, 1, 1, 6, 37, tzinfo=UTC)):
        print("unexpected schedule accepted", file=sys.stderr)
        return 1
    print("workflow validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
