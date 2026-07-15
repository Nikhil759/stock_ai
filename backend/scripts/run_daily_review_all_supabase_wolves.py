#!/usr/bin/env python3
"""Daily fund manager review for every active Supabase wolf.

Usage (from repo root):
    PYTHONPATH=.:backend python -m scripts.run_daily_review_all_supabase_wolves
    PYTHONPATH=.:backend python -m scripts.run_daily_review_all_supabase_wolves --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
for p in (_ROOT, _BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from deploy.daily_review_wolf import (
    print_daily_review_summary,
    run_daily_review_all_wolves,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daily fund manager review for all active Supabase wolves"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = run_daily_review_all_wolves(dry_run=args.dry_run)
    if not results:
        print("No active wolves.")
        return

    for result in results:
        print_daily_review_summary(result)

    print("\nJSON:")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
