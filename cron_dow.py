"""Normalize day-of-week for APScheduler CronTrigger.

Unix cron uses 0=Sunday and 1-5=Mon-Fri. APScheduler uses 0=Monday, so passing
Unix-style ``1-5`` runs Tue-Sat and skips Monday. Use ``mon-fri`` for weekdays.
"""


def normalize_apscheduler_dow(dow: str) -> str:
    d = (dow or "").strip().lower()
    if d in ("1-5", "1,2,3,4,5"):
        return "mon-fri"
    return d
