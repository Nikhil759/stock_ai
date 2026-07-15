#!/usr/bin/env python3
"""Chain Wolf Brain → Wolf Executor in dry-run (no DB writes).

Usage:
    PYTHONPATH=. .venv/bin/python -m wolf_executor.smoke_chain --mode deploy --strategy value
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
from wolf_executor import run_wolf_executor


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
    ap = argparse.ArgumentParser(description="Brain → Executor smoke chain")
    ap.add_argument("--mode", choices=("deploy", "daily_review"), default="deploy")
    ap.add_argument("--strategy", default="value")
    ap.add_argument("--wolf-id", default="WSMOKE01")
    ap.add_argument("--cash", type=float, default=10_000)
    ap.add_argument(
        "--brain-only",
        action="store_true",
        help="Skip executor (print brain output only)",
    )
    args = ap.parse_args()

    guardrails = {
        "stop_loss_pct": 15,
        "max_daily_loss_pct": 5,
        "max_capital_deployed_pct": 100,
        "max_per_stock_pct": 40,
        "min_trade_value": 1000,
    }
    shortlist = load_shortlist(args.strategy, date.today())
    mc = _market_context()
    holdings: list[dict] = []

    if args.mode == "daily_review" and shortlist:
        holdings = [
            {
                "symbol": shortlist[0]["symbol"],
                "quantity": 5,
                "avg_buy_price": 400,
                "current_price": 410,
                "target": 480,
                "stop_loss": 340,
            }
        ]

    brain = run_wolf_brain(
        wolf_id=args.wolf_id,
        mode=args.mode,
        trade_strategy=args.strategy,
        guardrails=guardrails,
        cash_available=args.cash,
        shortlist=shortlist,
        market_context=mc,
        current_holdings=holdings if args.mode == "daily_review" else None,
        birth_intent="Smoke chain — value thesis placeholder.",
    )
    print("=== brain ===")
    print(json.dumps(brain, indent=2, default=str))

    if args.brain_only:
        return

    sells = []
    buys = brain.get("picks") or brain.get("new_picks") or []
    if args.mode == "daily_review":
        held = {h["symbol"]: h for h in holdings}
        for review in brain.get("holdings_review") or []:
            if review.get("verdict") == "sell":
                sym = review["symbol"]
                sells.append(
                    {
                        "symbol": sym,
                        "quantity": held.get(sym, {}).get("quantity", 0),
                        "reason": review.get("reasoning", ""),
                    }
                )

    exec_out = run_wolf_executor(
        args.wolf_id,
        "paper",
        sells=sells,
        buys=buys,
        cash_available=args.cash,
        holdings=holdings,
        guardrails=guardrails,
        dry_run=True,
    )
    print("=== executor (dry-run) ===")
    print(json.dumps(exec_out, indent=2, default=str))


if __name__ == "__main__":
    main()
