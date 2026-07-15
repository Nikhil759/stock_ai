"""In-process fund manager schedule — selector and morning deploy."""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

_selector_lock = threading.Lock()
_selector_running = False
_morning_lock = threading.Lock()
_morning_running = False

SELECTOR_CRON = os.getenv("FUND_SELECTOR_CRON", "30 3 * * 1-5").strip()
MORNING_CRON = os.getenv("FUND_MORNING_CRON", "45 3 * * 1-5").strip()


def _scheduler_enabled() -> bool:
    flag = os.getenv("FUND_SCHEDULER_ENABLED", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    # Default on when deployed to Railway; off locally to avoid surprise LLM calls.
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH"))


def _run_selector_job() -> None:
    global _selector_running
    with _selector_lock:
        if _selector_running:
            log.info("selector job already running — skip")
            return
        _selector_running = True
    try:
        log.info("starting daily selector for all running Wolves")
        from scripts.run_selector_all_wolves import run_selector_all_wolves

        run_selector_all_wolves()
        log.info("daily selector finished")
    except Exception:
        log.exception("daily selector failed")
    finally:
        _selector_running = False


def _run_morning_job() -> None:
    global _morning_running
    with _morning_lock:
        if _morning_running:
            log.info("morning deploy already running — skip")
            return
        _morning_running = True
    try:
        os.environ.setdefault("FUND_MANAGER_FILL_PRICE", "intention")
        log.info("starting morning deploy for all running Wolves")
        from scripts.run_morning_all_wolves import run_morning_all_wolves

        run_morning_all_wolves()
        log.info("morning deploy finished")
    except Exception:
        log.exception("morning deploy failed")
    finally:
        _morning_running = False


def _add_cron_job(sched, cron_expr: str, job_id: str, func) -> bool:
    parts = cron_expr.split()
    if len(parts) != 5:
        log.warning("invalid cron %r for %s — job disabled", cron_expr, job_id)
        return False
    from apscheduler.triggers.cron import CronTrigger

    minute, hour, dom, month, dow = parts
    sched.add_job(
        func,
        CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow),
        id=job_id,
        replace_existing=True,
    )
    log.info("scheduled %s: %s UTC", job_id, cron_expr)
    return True


def start_fund_scheduler() -> None:
    if not _scheduler_enabled():
        log.info("fund scheduler disabled (set FUND_SCHEDULER_ENABLED=1 to enable locally)")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.warning("APScheduler not installed — fund scheduler disabled")
        return

    sched = BackgroundScheduler(timezone="UTC")
    _add_cron_job(sched, SELECTOR_CRON, "fund_selector", _run_selector_job)
    _add_cron_job(sched, MORNING_CRON, "fund_morning", _run_morning_job)
    sched.start()
    log.info("fund scheduler started")
