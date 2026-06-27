from __future__ import annotations

from datetime import UTC, date, datetime, time

AUTOMATION_SCHEDULE_CRON = "37 * * * *"
AUTOMATION_INTERVAL_HOURS = 18


def expected_utc_cron_for_berlin_5am(day: date) -> str:
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    local = datetime.combine(day, time(5, 0), tzinfo=berlin)
    utc_hour = local.astimezone(UTC).hour
    return f"0 {utc_hour} * * *"


def should_run_for_schedule(
    event_name: str, schedule: str | None, scheduled_at: date | datetime
) -> bool:
    if event_name == "workflow_dispatch":
        return True
    if event_name != "schedule" or schedule is None:
        return False
    return schedule == AUTOMATION_SCHEDULE_CRON


def _as_utc_hour(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(second=0, microsecond=0)
    return datetime.combine(value, time(0), tzinfo=UTC)
