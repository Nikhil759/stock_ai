#!/usr/bin/env python3
"""Evening job for every running Wolf.

Usage (from backend/):
    python -m scripts.run_evening_all_wolves
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import database as db
from fund_manager.evening import print_evening_summary, run_evening_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Evening job all running Wolves")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-redeploy", action="store_true")
    args = parser.parse_args()

    db.init_db()
    bots = db.list_running_bots()
    if not bots:
        print("No running Wolves.")
        return
    for bot in bots:
        print(f"\n{'='*50}\nWolf {bot['id']} ({bot['strategy']})")
        summary = run_evening_job(
            bot["id"],
            dry_run=args.dry_run,
            skip_redeploy=args.skip_redeploy,
        )
        print_evening_summary(summary)


if __name__ == "__main__":
    main()
