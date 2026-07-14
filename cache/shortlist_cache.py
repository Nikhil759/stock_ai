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
