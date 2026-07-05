#!/usr/bin/env python3
"""Phase 1 smoke test — Kite auth, live prices, ledger state for one bot.

Usage (from backend/):
    python -m scripts.test_fund_manager_phase1
    python -m scripts.test_fund_manager_phase1 --bot-id 1
    python -m scripts.test_fund_manager_phase1 --tickers RELIANCE TCS INFY HDFCBANK
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure backend/ is on path when run as module
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import database as db
from fund_manager.kite_auth import get_kite, login_url
from fund_manager.ledger import BotLedger
from fund_manager.prices import get_prices
from workspace import LEGACY_WORKSPACE_ID

DEFAULT_TICKERS = ["RELIANCE", "TCS", "INFY", "HDFCBANK"]


def _pick_bot_id(explicit: int | None) -> int:
    if explicit is not None:
        bot = db.get_bot(explicit)
        if not bot:
            raise SystemExit(f"Bot {explicit} not found")
        return explicit

    bots = db.list_bots(LEGACY_WORKSPACE_ID, include_terminated=False)
    if not bots:
        raise SystemExit(
            "No bots found. Deploy a Wolf in the app first, or pass --bot-id."
        )
    return bots[0]["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fund manager Phase 1 smoke test")
    parser.add_argument("--bot-id", type=int, default=None, help="Bot to inspect")
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=DEFAULT_TICKERS,
        help="NSE tickers for live price check",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Discard cached token and re-authenticate",
    )
    args = parser.parse_args()

    db.init_db()

    print("=" * 60)
    print("PHASE 1 — Kite auth + prices + ledger")
    print("=" * 60)

    print(f"\nLogin URL (if needed): {login_url()}\n")

    kite = get_kite(force_login=args.force_login)
    profile = kite.profile()
    print(f"Authenticated as: {profile.get('user_name')} ({profile.get('user_id')})")

    print(f"\n--- Live prices ({len(args.tickers)} tickers) ---")
    prices = get_prices(args.tickers)
    for ticker in args.tickers:
        sym = ticker.upper()
        price = prices.get(sym)
        if price is not None:
            print(f"  {sym:12s}  ₹{price:,.2f}")
        else:
            print(f"  {sym:12s}  (no quote)")

    bot_id = _pick_bot_id(args.bot_id)
    ledger = BotLedger(bot_id)
    summary = ledger.summary()

    print(f"\n--- Ledger: Wolf {bot_id} ---")
    print(json.dumps(summary, indent=2))

    print("\n--- Recent actions ---")
    for row in ledger.recent_actions(5):
        print(f"  [{row['createdAt'][:19]}] {row['action']}: {row['detail']}")

    print("\nPhase 1 OK — auth, prices, and ledger readable.")


if __name__ == "__main__":
    main()
