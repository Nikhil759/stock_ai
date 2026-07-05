#!/usr/bin/env python3
"""Run daily selector for every running Wolf (Railway cron entrypoint).

Usage (from backend/):
    python -m scripts.run_selector_all_wolves
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import database as db
from scripts.run_selector_for_wolf import run_for_wolf


def run_selector_all_wolves() -> list[tuple[int, str]]:
    """Run selector for every running Wolf. Returns list of (bot_id, error) failures."""
    db.init_db()
    bots = db.list_running_bots()
    if not bots:
        print("No running Wolves found.")
        return []
    print(f"Running daily selector for {len(bots)} Wolf(s)...")
    errors: list[tuple[int, str]] = []
    for bot in bots:
        bid = bot["id"]
        try:
            print(f"\n--- Wolf {bid} ({bot['strategy']}) ---")
            run_for_wolf(bid)
        except Exception as exc:
            print(f"FAILED Wolf {bid}: {exc}")
            errors.append((bid, str(exc)))
    if errors:
        print(f"\n{len(errors)} failure(s):")
        for bid, msg in errors:
            print(f"  Wolf {bid}: {msg}")
    else:
        print("\nAll Wolves processed.")
    return errors


def main() -> None:
    errors = run_selector_all_wolves()
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
