#!/usr/bin/env python3
"""Mac live-trading daily job — deferred births + live wolf fund manager review.

Usage (from repo root, WOLF_LIVE_ORDERS_ENABLED=1):
    PYTHONPATH=.:backend python -m scripts.run_live_trading_daily
    PYTHONPATH=.:backend python -m scripts.run_live_trading_daily --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
for p in (_ROOT, _BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from deploy.deploy_wolf import complete_all_deferred_live_births
from deploy.daily_review_wolf import (
    print_daily_review_summary,
    run_daily_review_all_wolves,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live trading daily: birth catch-up + live wolf review"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    birth_results = complete_all_deferred_live_births(dry_run=args.dry_run)
    if birth_results:
        print("=== Deferred live births ===")
        print(json.dumps(birth_results, indent=2, default=str))

    review_results = run_daily_review_all_wolves(
        dry_run=args.dry_run,
        wolf_mode="live",
    )
    if not review_results:
        print("No active live wolves for daily review.")
        return

    for result in review_results:
        print_daily_review_summary(result)

    print("\nJSON:")
    print(json.dumps(review_results, indent=2, default=str))


if __name__ == "__main__":
    main()
