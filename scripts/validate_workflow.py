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
    if not should_run_for_schedule(
        "schedule", AUTOMATION_SCHEDULE_CRON, datetime(2026, 1, 1, 0, 17, tzinfo=UTC)
    ):
        print("first 18-hour slot failed", file=sys.stderr)
        return 1
    if should_run_for_schedule(
        "schedule", AUTOMATION_SCHEDULE_CRON, datetime(2026, 1, 1, 6, 17, tzinfo=UTC)
    ):
        print("six-hour skip gate failed", file=sys.stderr)
        return 1
    if not should_run_for_schedule(
        "schedule", AUTOMATION_SCHEDULE_CRON, datetime(2026, 1, 1, 18, 17, tzinfo=UTC)
    ):
        print("second 18-hour slot failed", file=sys.stderr)
        return 1
    print("workflow validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
