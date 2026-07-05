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
from .config import DOSSIER_API_TOKEN, BUILD_CRON, get_dossier_dir
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


@app.post("/api/build")
def trigger_build(_: None = Depends(_verify_token)):
    if _build_running:
        return JSONResponse({"status": "already_running"}, status_code=409)
    threading.Thread(target=_run_build_job, daemon=True).start()
    return {"status": "started"}


def _start_scheduler() -> None:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("APScheduler not installed — scheduled builds disabled")
        return

    parts = BUILD_CRON.split()
    if len(parts) != 5:
        log.warning("invalid DOSSIER_BUILD_CRON=%r — scheduler disabled", BUILD_CRON)
        return

    minute, hour, dom, month, dow = parts
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        _run_build_job,
        CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow),
        id="dossier_build",
        replace_existing=True,
    )
    sched.start()
    log.info("scheduled dossier build: %s UTC", BUILD_CRON)


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
