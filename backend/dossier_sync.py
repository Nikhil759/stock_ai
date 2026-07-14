"""Pull dossiers from data-layer-cron internal API into a local cache."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent / "dossier_cache"


def _api_config() -> tuple[str, str] | None:
    url = (os.getenv("DOSSIER_API_URL") or "").strip().rstrip("/")
    if not url:
        return None
    token = (os.getenv("DOSSIER_API_TOKEN") or "").strip()
    return url, token


def sync_dossiers_from_api() -> dict:
    """Fetch dossiers from data-layer-cron and write to backend/dossier_cache/.

    Sets DOSSIER_DIR so data_layer.storage reads the synced copy.
    Returns a status dict for logging / API responses.
    """
    cfg = _api_config()
    if cfg is None:
        return {"synced": False, "source": "local", "message": "DOSSIER_API_URL not set"}

    api_url, token = cfg
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    log.info("syncing dossiers from %s", api_url)
    try:
        manifest = requests.get(
            f"{api_url}/api/dossiers/manifest",
            headers=headers,
            timeout=30,
        )
        manifest.raise_for_status()
        meta = manifest.json()
        log.info("remote manifest: %d dossiers as_of=%s", meta.get("count"), meta.get("as_of"))

        resp = requests.get(f"{api_url}/api/dossiers", headers=headers, timeout=180)
        resp.raise_for_status()
        dossiers = resp.json()
    except requests.RequestException as exc:
        log.error("dossier sync failed: %s", exc)
        raise RuntimeError(f"Could not sync dossiers from {api_url}: {exc}") from exc

    if not dossiers:
        raise RuntimeError(f"Dossier API at {api_url} returned zero dossiers")

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for old in _CACHE_DIR.glob("*.json"):
        old.unlink()

    for d in dossiers:
        ticker = (d.get("meta") or {}).get("ticker")
        if not ticker:
            continue
        (_CACHE_DIR / f"{ticker}.json").write_text(json.dumps(d, default=str))

    os.environ["DOSSIER_DIR"] = str(_CACHE_DIR)
    log.info("synced %d dossiers -> %s", len(dossiers), _CACHE_DIR)
    return {
        "synced": True,
        "source": api_url,
        "count": len(dossiers),
        "as_of": meta.get("as_of"),
        "cache_dir": str(_CACHE_DIR),
    }


def trigger_pipeline_run() -> dict:
    """Ask data-layer-cron to start the full morning pipeline in the background."""
    cfg = _api_config()
    if cfg is None:
        return {"started": False, "message": "DOSSIER_API_URL not set on stock_ai"}

    api_url, token = cfg
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    log.info("triggering full pipeline on %s", api_url)
    try:
        resp = requests.post(
            f"{api_url}/api/pipeline",
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        log.error("pipeline trigger failed: %s", exc)
        raise RuntimeError(f"Could not reach dossier API at {api_url}: {exc}") from exc

    if resp.status_code == 409:
        try:
            data = resp.json()
        except Exception:
            data = {"status": "already_running"}
        return {"started": False, **data}
    if resp.status_code >= 400:
        raise RuntimeError(f"Pipeline trigger failed ({resp.status_code}): {resp.text}")

    data = resp.json() if resp.content else {}
    return {"started": True, **data}
