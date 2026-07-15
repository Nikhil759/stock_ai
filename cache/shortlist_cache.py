"""
Phase D — shortlist cache.

Key format: shortlist_{strategy}_{YYYY-MM-DD}
Stores buy/watch survivors with frozen dossier prices. Same-day writes overwrite.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from data_layer.config import CACHE_DIR

SHORTLIST_DIR = CACHE_DIR / "shortlists"


def shortlist_path(strategy: str, as_of: date | str) -> Path:
    if isinstance(as_of, date):
        day = as_of.isoformat()
    else:
        day = str(as_of)
    strategy = strategy.lower().strip()
    return SHORTLIST_DIR / f"shortlist_{strategy}_{day}.json"


def save_shortlist(
    strategy: str,
    as_of: date | str,
    candidates: list[dict[str, Any]],
) -> Path:
    """Overwrite today's shortlist for this strategy (buy/watch entries)."""
    if isinstance(as_of, str):
        day = as_of
    else:
        day = as_of.isoformat()

    strategy = strategy.lower().strip()
    path = shortlist_path(strategy, day)
    SHORTLIST_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "strategy": strategy,
        "date": day,
        "count": len(candidates),
        "candidates": [
            {
                "symbol": c["symbol"],
                "conviction": c["conviction"],
                "verdict": c["verdict"],
                "reasoning": c["reasoning"],
                "price": c.get("price"),  # frozen at scoring time
                "date": day,
            }
            for c in candidates
        ],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(
        f"[SHORTLIST CACHE] Saved shortlist_{strategy}_{day} "
        f"({len(candidates)} candidates) → {path}"
    )
    return path


def load_shortlist(strategy: str, as_of: date | str) -> list[dict[str, Any]]:
    """Load candidates for strategy/date, or [] if missing."""
    path = shortlist_path(strategy, as_of)
    if not path.exists():
        print(f"[SHORTLIST CACHE] Miss — {path.name} not found")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    candidates = data.get("candidates") or []
    print(
        f"[SHORTLIST CACHE] Loaded shortlist_{strategy.lower()}_"
        f"{data.get('date', as_of)} ({len(candidates)} candidates)"
    )
    return candidates


def fetch_shortlists_from_cron() -> dict[str, list[dict[str, Any]]]:
    """Pull today's shortlists from data-layer-cron (volume-backed cache)."""
    import os

    import requests

    base = (os.getenv("DOSSIER_API_URL") or "").strip().rstrip("/")
    if not base:
        return {}
    token = (os.getenv("DOSSIER_API_TOKEN") or "").strip()
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            f"{base}/api/shortlists/today",
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            print(
                f"[SHORTLIST CACHE] Cron fetch failed ({resp.status_code}) "
                f"from {base}/api/shortlists/today"
            )
            return {}
        data = resp.json()
        sl = data.get("shortlists") or {}
        return sl if isinstance(sl, dict) else {}
    except Exception as exc:
        print(f"[SHORTLIST CACHE] Cron fetch error: {exc}")
        return {}


def load_shortlist_resolved(strategy: str, as_of: date | str) -> list[dict[str, Any]]:
    """Local disk first, then data-layer-cron API when stock_ai has no volume cache."""
    local = load_shortlist(strategy, as_of)
    if local:
        return local

    key = strategy.lower().strip()
    remote = fetch_shortlists_from_cron()
    cands = remote.get(key) or []
    if not cands:
        print(f"[SHORTLIST CACHE] Miss — no local or remote shortlist for {key}")
        return []

    print(f"[SHORTLIST CACHE] Remote hit — {len(cands)} candidates for {key}")
    try:
        save_shortlist(key, as_of, cands)
    except OSError as exc:
        print(f"[SHORTLIST CACHE] Could not persist remote shortlist: {exc}")
    return cands
