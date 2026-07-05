"""Per-Wolf intentions files — birth snapshot + daily handoff."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from data_paths import get_intentions_dir


def intentions_dir() -> Path:
    d = get_intentions_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def wolf_daily_path(bot_id: int, run_date: date | str | None = None) -> Path:
    d = run_date or date.today()
    if isinstance(d, date):
        d = d.isoformat()
    return intentions_dir() / f"wolf_{bot_id}_{d}.json"


def wolf_birth_path(bot_id: int) -> Path:
    return intentions_dir() / f"wolf_{bot_id}_birth.json"


def legacy_strategy_path(strategy: str, run_date: date | str | None = None) -> Path:
    """Deprecated shared-per-strategy path (pre per-Wolf intentions)."""
    d = run_date or date.today()
    if isinstance(d, date):
        d = d.isoformat()
    return intentions_dir() / f"{strategy}_{d}.json"


def write_birth_intention_file(bot_id: int, payload: dict) -> Path:
    path = wolf_birth_path(bot_id)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_intentions_for_bot(
    bot_id: int,
    strategy: str,
    run_date: date | str | None = None,
) -> dict:
    """Load today's intentions for a Wolf. Falls back to legacy strategy file."""
    path = wolf_daily_path(bot_id, run_date)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    legacy = legacy_strategy_path(strategy, run_date)
    if legacy.exists():
        return json.loads(legacy.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"No intentions for Wolf {bot_id} ({path.name})")


def load_picks_for_bot(
    bot_id: int,
    strategy: str,
    run_date: date | str | None = None,
) -> list[dict]:
    data = load_intentions_for_bot(bot_id, strategy, run_date)
    return list(data.get("result", {}).get("picks") or [])


def write_daily_intentions(bot_id: int, payload: dict) -> Path:
    d = payload.get("date") or date.today().isoformat()
    path = wolf_daily_path(bot_id, d)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
