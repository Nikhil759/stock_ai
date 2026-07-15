#!/usr/bin/env python3
"""CLI smoke test for Supabase deploy (requires DATABASE_URL + GEMINI_API_KEY).

Usage:
    PYTHONPATH=. .venv/bin/python scripts/smoke_deploy_wolf.py --strategy value --budget 10000
    PYTHONPATH=. .venv/bin/python scripts/smoke_deploy_wolf.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from deploy.deploy_wolf import (
    build_deploy_screen_response,
    deploy_new_wolf,
    guardrails_from_deploy_request,
    resolve_deploy_user_id,
)

DEFAULT_USER = UUID("11111111-1111-1111-1111-111111111111")


def main() -> None:
    ap = argparse.ArgumentParser(description="Supabase wolf deploy smoke test")
    ap.add_argument("--strategy", default="value")
    ap.add_argument("--budget", type=int, default=10_000)
    ap.add_argument("--user-id", default=None, help="UUID; defaults to WOLF_DEPLOY_USER_ID or seed user")
    ap.add_argument("--dry-run", action="store_true", help="Brain+executor simulation only (no DB writes)")
    args = ap.parse_args()

    user_id = resolve_deploy_user_id(args.user_id) or DEFAULT_USER
    guardrails = guardrails_from_deploy_request(
        stop_loss_pct=15,
        max_daily_loss_pct=5,
        max_deployed_pct=100,
        max_per_stock_pct=40,
    )

    if args.dry_run:
        print("dry-run: use wolf_executor.smoke_chain for brain→executor without DB")
        print(f"user_id={user_id} strategy={args.strategy} budget={args.budget}")
        return

    result = deploy_new_wolf(
        user_id=user_id,
        strategy=args.strategy,
        budget=args.budget,
        guardrails=guardrails,
    )
    screen = build_deploy_screen_response(
        result, strategy=args.strategy, allocation=args.budget
    )
    print(json.dumps({"wolf_id": result["wolf_id"], "screen": screen, "executor": result["executor"]}, indent=2, default=str))


if __name__ == "__main__":
    main()
