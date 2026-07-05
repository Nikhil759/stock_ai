"""Thin adapter over backend/database.py for the fund manager."""

from __future__ import annotations

from dataclasses import dataclass

import database as db


@dataclass
class Position:
    ticker: str
    shares: int
    entry_price: float
    stop: float
    target: float
    cost_basis: float
    ltp: float
    trade_id: int


class BotLedger:
    """Per-bot portfolio view — wraps existing SQLite state."""

    def __init__(self, bot_id: int):
        self.bot_id = bot_id

    def bot(self) -> dict:
        bot = db.get_bot(self.bot_id)
        if not bot:
            raise ValueError(f"Bot {self.bot_id} not found")
        return bot

    def cash_available(self) -> float:
        return self.bot()["availableCash"]

    def allocation(self) -> float:
        return self.bot()["allocation"]

    def portfolio_value(self) -> float:
        return self.bot()["portfolioValue"]

    def pnl(self) -> float:
        return self.bot()["pnl"]

    def deployed(self) -> float:
        return self.bot()["deployed"]

    def open_positions(self) -> list[Position]:
        trades = db.get_trades(self.bot_id, status="open")
        return [
            Position(
                ticker=t["ticker"],
                shares=t["qty"],
                entry_price=t["entry"],
                stop=t["stopLoss"],
                target=t["target"],
                cost_basis=round(t["qty"] * t["entry"], 2),
                ltp=t["ltp"],
                trade_id=t["id"],
            )
            for t in trades
        ]

    def open_tickers(self) -> set[str]:
        return {p.ticker for p in self.open_positions()}

    def realized_pnl(self) -> float:
        """Sum of closed-trade P&L (exit proceeds − cost basis)."""
        closed = db.get_trades(self.bot_id, status="closed")
        total = 0.0
        for t in closed:
            if t.get("exitPrice") is not None:
                total += t["qty"] * t["exitPrice"] - t["qty"] * t["entry"]
        return round(total, 2)

    def unrealized_pnl(self) -> float:
        """Mark-to-market P&L on open positions."""
        total = 0.0
        for p in self.open_positions():
            total += p.shares * p.ltp - p.cost_basis
        return round(total, 2)

    def append_log(self, action: str, detail: str = "", reasoning: str = "") -> None:
        db.log_action(self.bot_id, action, detail, reasoning)

    def recent_actions(self, limit: int = 10) -> list[dict]:
        return db.get_action_log(self.bot_id, limit=limit)

    def summary(self) -> dict:
        """Snapshot for logging / CLI output."""
        bot = self.bot()
        positions = self.open_positions()
        return {
            "botId": self.bot_id,
            "name": bot["name"],
            "strategy": bot["strategy"],
            "status": bot["status"],
            "allocation": bot["allocation"],
            "cashAvailable": bot["availableCash"],
            "deployed": bot["deployed"],
            "portfolioValue": bot["portfolioValue"],
            "pnl": bot["pnl"],
            "realizedPnl": self.realized_pnl(),
            "unrealizedPnl": self.unrealized_pnl(),
            "openPositionCount": len(positions),
            "positions": [
                {
                    "ticker": p.ticker,
                    "shares": p.shares,
                    "entry": p.entry_price,
                    "ltp": p.ltp,
                    "stop": p.stop,
                    "target": p.target,
                    "costBasis": p.cost_basis,
                }
                for p in positions
            ],
        }
