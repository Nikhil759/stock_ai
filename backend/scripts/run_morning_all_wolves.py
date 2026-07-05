#!/usr/bin/env python3
"""Morning deploy for every running Wolf.

Usage (from backend/):
    python -m scripts.run_morning_all_wolves
    FUND_MANAGER_FILL_PRICE=intention python -m scripts.run_morning_all_wolves --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import database as db
from fund_manager.deploy import morning_deploy, print_deploy_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Morning deploy all running Wolves")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()

    db.init_db()
    bots = db.list_running_bots()
    if not bots:
        print("No running Wolves.")
        return
    for bot in bots:
        print(f"\n{'='*50}\nWolf {bot['id']} ({bot['strategy']})")
        summary = morning_deploy(bot["id"], run_date=args.date, dry_run=args.dry_run)
        print_deploy_summary(summary)


if __name__ == "__main__":
    main()
