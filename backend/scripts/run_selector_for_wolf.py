#!/usr/bin/env python3
"""Run selector pipeline for one Wolf — writes per-Wolf daily intentions.

Usage (from backend/):
    python -m scripts.run_selector_for_wolf --bot-id 13
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from repo_paths import ensure_repo_on_path

ensure_repo_on_path()

import database as db
from dossier_sync import sync_dossiers_from_api
from wolf_context import build_wolf_context, open_positions_for_account
from selector.pipeline import run_pipeline
from selector.schemas import FinalPicks


def run_for_wolf(bot_id: int) -> dict:
    db.init_db()
    bot = db.get_bot(bot_id)
    if not bot:
        raise SystemExit(f"Bot {bot_id} not found")
    if bot["status"] != "running":
        raise SystemExit(f"Bot {bot_id} is {bot['status']} — only running Wolves get daily intentions")

    sync = sync_dossiers_from_api()
    print(f"Dossier sync: {sync}")

    wolf_context = build_wolf_context(bot_id)
    if not wolf_context.get("birthIntention"):
        print(f"Warning: Wolf {bot_id} has no birth intention yet (deploy without screen?)")

    payload = run_pipeline(
        bot["strategy"],
        budget=int(bot["allocation"]),
        cash_available=float(bot["availableCash"]),
        per_stock_cap_pct=float(bot["max_per_stock_pct"]),
        use_llm=True,
        write_intentions=True,
        bot_id=bot_id,
        wolf_context=wolf_context,
        open_positions=open_positions_for_account(bot_id),
    )

    ipath = payload.get("intentionsPath") or ""
    fname = Path(ipath).name if ipath else "?"
    result = FinalPicks.model_validate(payload["result"])
    print(f"\n=== Wolf {bot_id} ({bot['strategy']}) — {fname} ===")
    for p in result.picks:
        print(f"  BUY {p.ticker}: {p.shares} sh @ ₹{p.buy_price:,.2f} — {p.rationale[:80]}...")
    if not result.picks:
        print("  No picks — holding cash.")
    print(f"  Cash held: ₹{result.cash_held_inr:,.0f}")
    print(f"  Note: {result.portfolio_note}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-Wolf daily selector")
    parser.add_argument("--bot-id", type=int, required=True)
    args = parser.parse_args()
    payload = run_for_wolf(args.bot_id)
    print("\nJSON summary:")
    print(json.dumps({
        "botId": args.bot_id,
        "intentionsPath": payload.get("intentionsPath"),
        "picks": [p["ticker"] for p in payload.get("result", {}).get("picks", [])],
    }, indent=2))


if __name__ == "__main__":
    main()
