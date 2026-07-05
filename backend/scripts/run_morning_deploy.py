#!/usr/bin/env python3
"""Morning deploy CLI — run fund manager against today's intentions file.

Usage (from backend/):
    python -m scripts.run_morning_deploy --bot-id 13
    python -m scripts.run_morning_deploy --bot-id 13 --dry-run
    python -m scripts.run_morning_deploy --bot-id 13 --date 2026-07-05
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import database as db
from fund_manager.deploy import morning_deploy, print_deploy_summary
from workspace import LEGACY_WORKSPACE_ID


def main() -> None:
    parser = argparse.ArgumentParser(description="Fund manager morning deploy")
    parser.add_argument("--bot-id", type=int, default=None)
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run gates without placing trades or pending records",
    )
    args = parser.parse_args()

    db.init_db()

    bot_id = args.bot_id
    if bot_id is None:
        bots = db.list_bots(LEGACY_WORKSPACE_ID)
        if not bots:
            raise SystemExit("No bots found — pass --bot-id")
        bot_id = bots[0]["id"]

    bot = db.get_bot(bot_id)
    if not bot:
        raise SystemExit(f"Bot {bot_id} not found")

    if args.dry_run:
        print("(dry-run — no trades will be placed)")

    summary = morning_deploy(bot_id, run_date=args.date, dry_run=args.dry_run)
    print_deploy_summary(summary)
    print("\nJSON:")
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()
