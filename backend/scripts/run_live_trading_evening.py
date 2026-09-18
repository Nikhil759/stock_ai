#!/usr/bin/env python3
"""Mac live-trading evening job — target/stop exits for live wolves only.

Usage (from repo root, WOLF_LIVE_ORDERS_ENABLED=1):
    PYTHONPATH=.:backend python -m scripts.run_live_trading_evening
    PYTHONPATH=.:backend python -m scripts.run_live_trading_evening --dry-run
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

from wolf_evening import print_evening_summary, run_evening_all_wolves


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live trading evening exits for live wolves"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = run_evening_all_wolves(dry_run=args.dry_run, wolf_mode="live")
    if not results:
        print("No active live wolves.")
        return

    for result in results:
        print_evening_summary(result)

    print("\nJSON:")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
