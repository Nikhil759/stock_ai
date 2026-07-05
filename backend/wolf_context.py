"""Build per-Wolf context for the selector — birth intention + trade history."""

from __future__ import annotations

import json
from typing import Any

import database as db


def _trade_summary(t: dict) -> dict:
    return {
        "ticker": t["ticker"],
        "shares": t["qty"],
        "entry": t["entry"],
        "ltp": t.get("ltp"),
        "stop": t.get("stopLoss"),
        "target": t.get("target"),
        "entryDate": t.get("entryDate"),
        "status": t.get("status"),
        "exitPrice": t.get("exitPrice"),
        "exitDate": t.get("exitDate"),
        "exitReason": t.get("exitReason"),
        "source": t.get("source"),
    }


def build_wolf_context(bot_id: int) -> dict[str, Any]:
    """Snapshot for daily intention LLM: birth thesis, book, and history."""
    bot = db.get_bot(bot_id)
    if not bot:
        raise ValueError(f"Bot {bot_id} not found")

    birth = db.get_birth_intention(bot_id)
    open_trades = db.get_trades(bot_id, status="open")
    closed_trades = db.get_trades(bot_id, status="closed")
    closed_trades.sort(key=lambda t: t.get("exitDate") or t.get("entryDate") or "", reverse=True)
    recent_closed = [_trade_summary(t) for t in closed_trades[:20]]

    open_positions = []
    for t in open_trades:
        row = _trade_summary(t)
        row["costBasis"] = round(t["qty"] * t["entry"], 2)
        open_positions.append(row)

    closed_pnl = 0.0
    for t in closed_trades:
        if t.get("exitPrice") is not None:
            closed_pnl += t["qty"] * t["exitPrice"] - t["qty"] * t["entry"]

    held_tickers = {t["ticker"] for t in open_trades}

    return {
        "botId": bot_id,
        "name": bot["name"],
        "strategy": bot["strategy"],
        "deployedAt": bot.get("deployedAt"),
        "status": bot["status"],
        "portfolio": {
            "allocation": bot["allocation"],
            "cashAvailable": bot["availableCash"],
            "deployed": bot["deployed"],
            "portfolioValue": bot["portfolioValue"],
            "pnl": bot["pnl"],
            "realizedPnlClosed": round(closed_pnl, 2),
        },
        "birthIntention": birth,
        "openPositions": open_positions,
        "heldTickers": sorted(held_tickers),
        "closedTrades": recent_closed,
        "closedTradeCount": len(closed_trades),
    }


def open_positions_for_account(bot_id: int) -> list[dict]:
    """Account.open_positions shape for the final-selection prompt."""
    ctx = build_wolf_context(bot_id)
    return [
        {
            "ticker": p["ticker"],
            "shares": p["shares"],
            "entry": p["entry"],
            "stop": p["stop"],
            "target": p["target"],
            "cost_basis": p.get("costBasis"),
        }
        for p in ctx["openPositions"]
    ]
