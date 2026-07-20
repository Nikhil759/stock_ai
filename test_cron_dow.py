"""Tests for APScheduler day-of-week normalization."""
from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger

from cron_dow import normalize_apscheduler_dow


def test_normalize_unix_weekdays_to_mon_fri() -> None:
    assert normalize_apscheduler_dow("1-5") == "mon-fri"
    assert normalize_apscheduler_dow("MON-FRI") == "mon-fri"


def test_monday_fires_with_mon_fri_not_unix_1_5() -> None:
    """Regression: Unix 1-5 skips Monday in APScheduler (runs Tue-Sat)."""
    start = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)  # Monday

    unix_style = CronTrigger(
        minute="30", hour="2", day="*", month="*", day_of_week="1-5", timezone="UTC"
    )
    assert unix_style.get_next_fire_time(None, start).date().isoformat() == "2026-07-21"

    fixed = CronTrigger(
        minute="30",
        hour="2",
        day="*",
        month="*",
        day_of_week=normalize_apscheduler_dow("1-5"),
        timezone="UTC",
    )
    assert fixed.get_next_fire_time(None, start).date().isoformat() == "2026-07-20"


if __name__ == "__main__":
    test_normalize_unix_weekdays_to_mon_fri()
    test_monday_fires_with_mon_fri_not_unix_1_5()
    print("cron_dow tests ok")
