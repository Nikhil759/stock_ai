"""In-process APScheduler for Supabase daily fund manager review."""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

_daily_lock = threading.Lock()
_daily_running = False

# Default 25 3 * * mon-fri UTC ≈ 8:55 AM IST (after market open buffer).
DAILY_CRON = os.getenv("WOLF_DAILY_CRON", "25 3 * * mon-fri").strip()


def _scheduler_enabled() -> bool:
    flag = os.getenv("WOLF_DAILY_SCHEDULER_ENABLED", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH"))


def _run_daily_job() -> None:
    global _daily_running
    with _daily_lock:
        if _daily_running:
            log.info("supabase daily review already running — skip")
            return
        _daily_running = True
    try:
        log.info("starting supabase daily fund manager for all active wolves")
        from deploy.daily_review_wolf import run_daily_review_all_wolves

        run_daily_review_all_wolves()
        log.info("supabase daily fund manager finished")
    except Exception:
        log.exception("supabase daily fund manager failed")
    finally:
        _daily_running = False


def _add_cron_job(sched, cron_expr: str, job_id: str, func) -> bool:
    parts = cron_expr.split()
    if len(parts) != 5:
        log.warning("invalid cron %r for %s — job disabled", cron_expr, job_id)
        return False
    from apscheduler.triggers.cron import CronTrigger

    minute, hour, dom, month, dow = parts
    from cron_dow import normalize_apscheduler_dow

    dow = normalize_apscheduler_dow(dow)
    sched.add_job(
        func,
        CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow),
        id=job_id,
        replace_existing=True,
    )
    log.info("scheduled %s: %s UTC", job_id, cron_expr)
    return True


def start_wolf_daily_scheduler() -> None:
    if not _scheduler_enabled():
        log.info(
            "wolf daily scheduler disabled "
            "(set WOLF_DAILY_SCHEDULER_ENABLED=1 to enable locally)"
        )
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.warning("APScheduler not installed — wolf daily scheduler disabled")
        return

    sched = BackgroundScheduler(timezone="UTC")
    if (DAILY_CRON or "").strip():
        _add_cron_job(sched, DAILY_CRON, "wolf_daily_review", _run_daily_job)
    else:
        log.info("wolf daily review disabled (WOLF_DAILY_CRON empty)")
    sched.start()
    log.info("wolf daily scheduler started")
