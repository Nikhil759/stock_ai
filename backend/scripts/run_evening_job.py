#!/usr/bin/env python3
"""Evening job CLI — refresh, exits, redeploy, circuit breaker.

Usage (from backend/):
    python -m scripts.run_evening_job --bot-id 13
    python -m scripts.run_evening_job --bot-id 13 --dry-run
    python -m scripts.run_evening_job --bot-id 13 --skip-redeploy
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
from fund_manager.evening import print_evening_summary, run_evening_job
from workspace import LEGACY_WORKSPACE_ID


def main() -> None:
    parser = argparse.ArgumentParser(description="Fund manager evening job")
    parser.add_argument("--bot-id", type=int, default=None)
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD for intentions lookup")
    parser.add_argument("--dry-run", action="store_true", help="Gates only — no trades from redeploy")
    parser.add_argument("--skip-redeploy", action="store_true", help="Refresh/exit only, no brain call")
    args = parser.parse_args()

    db.init_db()

    bot_id = args.bot_id
    if bot_id is None:
        bots = db.list_bots(LEGACY_WORKSPACE_ID)
        if not bots:
            raise SystemExit("No bots found — pass --bot-id")
        bot_id = bots[0]["id"]

    if args.dry_run:
        print("(dry-run redeploy — no trades from brain decisions)")

    summary = run_evening_job(
        bot_id,
        run_date=args.date,
        dry_run=args.dry_run,
        skip_redeploy=args.skip_redeploy,
    )
    print_evening_summary(summary)
    print("\nJSON:")
    print(json.dumps(summary.to_dict(), indent=2))


if __name__ == "__main__":
    main()
