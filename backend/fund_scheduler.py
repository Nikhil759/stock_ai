"""In-process fund manager schedule — Kite token refresh, selector, morning deploy."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_kite_lock = threading.Lock()
_kite_running = False
_selector_lock = threading.Lock()
_selector_running = False
_morning_lock = threading.Lock()
_morning_running = False

# 00:30 UTC = 6:00 AM IST — Zerodha access tokens expire ~6 AM IST each day.
KITE_REFRESH_CRON = os.getenv("KITE_REFRESH_CRON", "30 0 * * 1-5").strip()
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


def _load_kite_auth():
    """Load kite_auth by file path — avoids fund_manager.__init__ heavy imports."""
    backend = Path(__file__).resolve().parent
    path = backend / "fund_manager" / "kite_auth.py"
    backend_str = str(backend)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)
    spec = importlib.util.spec_from_file_location("_fund_sched_kite_auth", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_kite_refresh_job() -> None:
    global _kite_running
    with _kite_lock:
        if _kite_running:
            log.info("kite refresh already running — skip")
            return
        _kite_running = True
    try:
        log.info("starting daily Kite token refresh")
        kite_auth = _load_kite_auth()
        if kite_auth is None:
            log.warning("kite refresh SKIP — kite_auth.py not found")
            return
        if not kite_auth.totp_configured():
            log.warning(
                "kite refresh SKIP — set KITE_USER_ID / KITE_PASSWORD / KITE_TOTP_SECRET"
            )
            return
        if os.getenv("RAILWAY_ENVIRONMENT"):
            log.warning(
                "kite refresh SKIP on Railway — Zerodha blocks TOTP from cloud IPs; "
                "paste request_token on /health instead"
            )
            return
        token = kite_auth.refresh_access_token(force=True)
        kite = kite_auth.get_kite()
        profile = kite.profile()
        log.info(
            "kite refresh OK — %s (%s) token=%s (%d chars)",
            profile.get("user_name"),
            profile.get("user_id"),
            kite_auth._token_path().name,
            len(token),
        )
    except Exception:
        log.exception("kite refresh failed")
    finally:
        _kite_running = False


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
    _add_cron_job(sched, KITE_REFRESH_CRON, "kite_refresh", _run_kite_refresh_job)
    _add_cron_job(sched, SELECTOR_CRON, "fund_selector", _run_selector_job)
    _add_cron_job(sched, MORNING_CRON, "fund_morning", _run_morning_job)
    sched.start()
    log.info("fund scheduler started")
