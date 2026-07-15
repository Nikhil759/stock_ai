#!/usr/bin/env python3
"""Smoke-test Wolf Brain against today's shortlist (requires GEMINI_API_KEY).

Usage:
    PYTHONPATH=. python -m wolf_brain.smoke_test --mode deploy --strategy value
    PYTHONPATH=. python -m wolf_brain.smoke_test --mode daily_review --strategy value --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from cache.shortlist_cache import load_shortlist
from data_layer.storage import load_all_dossiers
from wolf_brain import run_wolf_brain


def _market_context() -> dict:
    dossiers = load_all_dossiers()
    if not dossiers:
        return {}
    mc = dossiers[0].market_context
    d = mc.__dict__ if hasattr(mc, "__dict__") else mc
    return {
        "nifty_trend": d.get("nifty_trend"),
        "vix": d.get("india_vix"),
        "nifty_above_200dma": d.get("nifty_above_200dma"),
        "market_breadth_pct_above_200dma": d.get("market_breadth_pct_above_200dma"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Wolf Brain smoke test")
    ap.add_argument("--mode", choices=("deploy", "daily_review"), default="deploy")
    ap.add_argument("--strategy", default="value")
    ap.add_argument("--wolf-id", default="WSMOKE01")
    ap.add_argument("--cash", type=float, default=10_000)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Gemini; only print shortlist + market context",
    )
    args = ap.parse_args()

    shortlist = load_shortlist(args.strategy, date.today())
    mc = _market_context()
    guardrails = {
        "stop_loss_pct": 15,
        "max_daily_loss_pct": 5,
        "max_capital_deployed_pct": 100,
        "max_per_stock_pct": 40,
        "min_trade_value": 1000,
    }

    print(f"shortlist: {len(shortlist)} candidates")
    print(f"market_context: {json.dumps(mc, default=str)}")

    if args.dry_run:
        print("dry-run — skipping run_wolf_brain")
        return

    holdings = []
    if args.mode == "daily_review":
        holdings = [
            {
                "symbol": shortlist[0]["symbol"] if shortlist else "ITC",
                "quantity": 5,
                "avg_buy_price": 400,
                "current_price": 410,
                "target": 480,
                "stop_loss": 340,
                "unrealized_pl_pct": 2.5,
                "days_held": 3,
            }
        ]

    result = run_wolf_brain(
        wolf_id=args.wolf_id,
        mode=args.mode,
        trade_strategy=args.strategy,
        guardrails=guardrails,
        cash_available=args.cash,
        shortlist=shortlist,
        market_context=mc,
        current_holdings=holdings if args.mode == "daily_review" else None,
        birth_intent="Smoke test wolf — value thesis placeholder.",
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
