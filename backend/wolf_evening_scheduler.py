"""In-process APScheduler for Supabase evening auto-exits."""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

_evening_lock = threading.Lock()
_evening_running = False

# Default 11:30 UTC Mon–Fri ≈ 5:00 PM IST (after post-close dossier refresh).
EVENING_CRON = os.getenv("WOLF_EVENING_CRON", "30 11 * * mon-fri").strip()


def _scheduler_enabled() -> bool:
    flag = os.getenv("WOLF_EVENING_SCHEDULER_ENABLED", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH"))


def _run_evening_job() -> None:
    global _evening_running
    with _evening_lock:
        if _evening_running:
            log.info("supabase evening job already running — skip")
            return
        _evening_running = True
    try:
        log.info("starting supabase evening job for all active wolves")
        from wolf_evening import run_evening_all_wolves

        run_evening_all_wolves()
        log.info("supabase evening job finished")
    except Exception:
        log.exception("supabase evening job failed")
    finally:
        _evening_running = False


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


def start_wolf_evening_scheduler() -> None:
    if not _scheduler_enabled():
        log.info(
            "wolf evening scheduler disabled "
            "(set WOLF_EVENING_SCHEDULER_ENABLED=1 to enable locally)"
        )
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.warning("APScheduler not installed — wolf evening scheduler disabled")
        return

    sched = BackgroundScheduler(timezone="UTC")
    if (EVENING_CRON or "").strip():
        _add_cron_job(sched, EVENING_CRON, "wolf_evening", _run_evening_job)
    else:
        log.info("wolf evening job disabled (WOLF_EVENING_CRON empty)")
    sched.start()
    log.info("wolf evening scheduler started")
