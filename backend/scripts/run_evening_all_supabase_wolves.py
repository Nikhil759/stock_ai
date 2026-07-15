#!/usr/bin/env python3
"""Evening auto-exit job for every active Supabase wolf.

Usage (from backend/):
    python -m scripts.run_evening_all_supabase_wolves
    python -m scripts.run_evening_all_supabase_wolves --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
for p in (_ROOT, _BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from wolf_evening import print_evening_summary, run_evening_all_wolves


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evening auto-exit for all active Supabase wolves"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = run_evening_all_wolves(dry_run=args.dry_run)
    if not results:
        print("No active wolves.")
        return

    for result in results:
        print_evening_summary(result)

    print("\nJSON:")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
