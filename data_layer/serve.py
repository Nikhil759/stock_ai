"""
Internal dossier API for Railway private networking.

Runs on data-layer-cron (persistent service + volume). stock_ai syncs dossiers
from here before screening.

Start:  uvicorn data_layer.serve:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse

from . import config
from .build import run as run_build
from .config import (
    BUILD_CRON,
    DOSSIER_API_TOKEN,
    POST_CLOSE_BUILD_CRON,
    get_dossier_dir,
)
from .storage import load_all_dossiers

load_dotenv(config.ROOT / ".env")

log = logging.getLogger(__name__)
app = FastAPI(title="Wolf Dossier API", version="1.0.0")

_build_lock = threading.Lock()
_build_running = False


def _verify_token(authorization: str | None = Header(None)) -> None:
    if not DOSSIER_API_TOKEN:
        return  # local dev — no token required
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != DOSSIER_API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


def _dossier_stats() -> dict:
    dossiers = load_all_dossiers()
    as_of = dossiers[0].meta.as_of if dossiers else None
    return {
        "count": len(dossiers),
        "as_of": as_of,
        "dossier_dir": str(get_dossier_dir()),
    }


def _run_build_job() -> None:
    """Dossier build only — used by /api/build and the volume-empty bootstrap."""
    global _build_running
    with _build_lock:
        if _build_running:
            log.info("build already running — skip")
            return
        _build_running = True
    try:
        log.info("starting dossier build")
        run_build(snapshot="pre_open")
        stats = _dossier_stats()
        log.info("build complete: %d dossiers (as_of=%s)", stats["count"], stats["as_of"])
    except Exception:
        log.exception("dossier build failed")
    finally:
        _build_running = False


def _run_full_pipeline_job() -> None:
    """Scheduled job: build dossiers -> funnels -> batch scoring -> health_status.

    This replaces the old build-only scheduled job now that Phase C/D/E live
    in this repo — one daily run keeps dossiers, shortlists, and the ops
    health dashboard all in sync.
    """
    global _build_running
    with _build_lock:
        if _build_running:
            log.info("pipeline already running — skip")
            return
        _build_running = True
    try:
        log.info("starting full morning pipeline (build + funnels + scoring)")
        from cron.morning_ingestion import run_pipeline

        run_pipeline()
        stats = _dossier_stats()
        log.info(
            "full pipeline complete: %d dossiers (as_of=%s)", stats["count"], stats["as_of"]
        )
    except Exception:
        log.exception("full morning pipeline failed")
    finally:
        _build_running = False


def _run_post_close_build_job() -> None:
    """Weekday post-close refresh — update dossiers without Marketaux or scoring."""
    global _build_running
    with _build_lock:
        if _build_running:
            log.info("post-close build already running — skip")
            return
        _build_running = True
    try:
        log.info("starting post-close dossier refresh (snapshot=post_close, skip_news)")
        run_build(snapshot="post_close", skip_news=True)
        stats = _dossier_stats()
        log.info(
            "post-close build complete: %d dossiers (as_of=%s)",
            stats["count"],
            stats["as_of"],
        )
    except Exception:
        log.exception("post-close dossier build failed")
    finally:
        _build_running = False


@app.get("/health")
def health():
    stats = _dossier_stats()
    return {"status": "ok", **stats}


@app.get("/api/dossiers/manifest")
def dossier_manifest(_: None = Depends(_verify_token)):
    dossiers = load_all_dossiers()
    return {
        "count": len(dossiers),
        "as_of": dossiers[0].meta.as_of if dossiers else None,
        "tickers": [d.meta.ticker for d in dossiers],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/dossiers")
def list_dossiers(_: None = Depends(_verify_token)):
    dossiers = load_all_dossiers()
    if not dossiers:
        raise HTTPException(status_code=404, detail="No dossiers on volume yet — trigger /api/build")
    return [d.to_dict() for d in dossiers]


@app.get("/api/shortlists/today")
def shortlists_today(_: None = Depends(_verify_token)):
    """Today's cached shortlists per strategy (from volume)."""
    from datetime import date

    from cache.shortlist_cache import load_shortlist

    day = date.today()
    strategies = ("value", "winners", "box", "dip")
    out: dict[str, list] = {}
    for name in strategies:
        cands = load_shortlist(name, day)
        if cands:
            out[name] = cands
    return {"date": day.isoformat(), "shortlists": out}


@app.post("/api/build")
def trigger_build(_: None = Depends(_verify_token)):
    if _build_running:
        return JSONResponse({"status": "already_running"}, status_code=409)
    threading.Thread(target=_run_build_job, daemon=True).start()
    return {"status": "started"}


@app.post("/api/build-close")
def trigger_post_close_build(_: None = Depends(_verify_token)):
    """Post-close dossier refresh only (no news API, no scoring)."""
    if _build_running:
        return JSONResponse({"status": "already_running"}, status_code=409)
    threading.Thread(target=_run_post_close_build_job, daemon=True).start()
    return {"status": "started", "snapshot": "post_close", "skip_news": True}


@app.post("/api/pipeline")
def trigger_pipeline(_: None = Depends(_verify_token)):
    """Start full morning pipeline (build → funnels → LLM scoring → health_status)."""
    if _build_running:
        return JSONResponse({"status": "already_running"}, status_code=409)
    threading.Thread(target=_run_full_pipeline_job, daemon=True).start()
    return {"status": "started"}


def _add_cron_job(sched, cron_expr: str, job_id: str, func) -> bool:
    from apscheduler.triggers.cron import CronTrigger

    parts = cron_expr.split()
    if len(parts) != 5:
        log.warning("invalid cron %r for %s — job disabled", cron_expr, job_id)
        return False
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


def _start_scheduler() -> None:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("APScheduler not installed — scheduled builds disabled")
        return

    sched = BackgroundScheduler(timezone="UTC")
    if not _add_cron_job(sched, BUILD_CRON, "morning_pipeline", _run_full_pipeline_job):
        log.warning("morning pipeline scheduler disabled — check DOSSIER_BUILD_CRON")
        return

    post_close = (POST_CLOSE_BUILD_CRON or "").strip()
    if post_close:
        _add_cron_job(
            sched, post_close, "post_close_build", _run_post_close_build_job
        )
    else:
        log.info("post-close build disabled (DOSSIER_POST_CLOSE_CRON empty)")

    sched.start()


@app.on_event("startup")
def startup():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    stats = _dossier_stats()
    log.info("dossier API ready — %d dossiers at %s", stats["count"], stats["dossier_dir"])
    _start_scheduler()
    if stats["count"] == 0:
        log.info("no dossiers on volume — kicking off initial build")
        threading.Thread(target=_run_build_job, daemon=True).start()
