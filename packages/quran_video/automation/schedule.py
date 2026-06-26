from __future__ import annotations

from datetime import UTC, date, datetime, time

AUTOMATION_SCHEDULE_CRON = "0 */6 * * *"
AUTOMATION_INTERVAL_HOURS = 18
AUTOMATION_ANCHOR_UTC = datetime(2026, 1, 1, tzinfo=UTC)


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
    if schedule != AUTOMATION_SCHEDULE_CRON:
        return False
    run_at = _as_utc_hour(scheduled_at)
    if run_at.minute != 0 or run_at.hour % 6 != 0:
        return False
    elapsed_hours = int((run_at - AUTOMATION_ANCHOR_UTC).total_seconds() // 3600)
    return elapsed_hours >= 0 and elapsed_hours % AUTOMATION_INTERVAL_HOURS == 0


def _as_utc_hour(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(second=0, microsecond=0)
    return datetime.combine(value, time(0), tzinfo=UTC)
