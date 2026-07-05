"""Nine-gate deploy sequence — deterministic capital deployment."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import database as db
from fund_manager.config import FILL_PRICE_MODE
from fund_manager.ledger import BotLedger
from fund_manager.prices import get_prices


class GateAction(str, Enum):
    EXECUTE = "execute"
    SKIP = "skip"
    HALT = "halt"
    PENDING = "pending"


@dataclass
class SizedOrder:
    ticker: str
    shares: int
    fill_price: float
    cost: float
    stop_loss: float
    sell_target: float
    rationale: str
    trimmed: bool = False
    source_pick: dict = field(default_factory=dict)


@dataclass
class GateResult:
    action: GateAction
    gate: int
    reason: str
    order: SizedOrder | None = None


def _fmt_inr(n: float) -> str:
    return f"₹{round(n):,}"


def _deployed_capital(open_trades: list[dict]) -> float:
    return sum(t["qty"] * t["entry"] for t in open_trades)


def _position_value_for_ticker(open_trades: list[dict], ticker: str) -> float:
    return sum(
        t["qty"] * t["entry"]
        for t in open_trades
        if t.get("ticker") == ticker and t.get("status") == "open"
    )


def is_breaker_tripped(bot_id: int) -> bool:
    """Gate 1 — circuit breaker."""
    bot = db.get_bot(bot_id)
    if not bot:
        return False
    return bool(bot.get("breakerTripped"))


def _fill_price(ticker: str, pick: dict) -> float | None:
    if FILL_PRICE_MODE == "intention":
        p = pick.get("buy_price")
        return round(float(p), 2) if p and p > 0 else None
    prices = get_prices([ticker])
    return prices.get(ticker.upper())


def _needs_approval(bot: dict, cost: float) -> bool:
    if bot.get("mode") == "advisory":
        return True
    if bot.get("mode") != "autonomous":
        return True
    level = bot.get("level", "A")
    if level == "A":
        return True
    if level == "B":
        return cost > bot.get("auto_threshold", 2000)
    return False  # level C — full auto


def run_gates(
    bot_id: int,
    pick: dict,
    *,
    open_trades: list[dict] | None = None,
    cash_override: float | None = None,
    skip_execute: bool = False,
) -> GateResult:
    """Process one intended buy through gates 1–9.

    Gates 1–4 reject (skip/halt). Gate 5 trims. Gates 6–7 size and check autonomy.
    Gates 8–9 execute paper buy unless skip_execute=True (dry-run).
    """
    ledger = BotLedger(bot_id)
    bot = ledger.bot()
    trades = open_trades if open_trades is not None else db.get_trades(bot_id, status="open")
    cash = cash_override if cash_override is not None else ledger.cash_available()
    allocation = bot["allocation"]

    # Gate 1 — circuit breaker
    if is_breaker_tripped(bot_id):
        return GateResult(GateAction.HALT, 1, "Daily-loss circuit breaker tripped — all buys halted.")

    if bot["status"] in ("paused", "terminated"):
        return GateResult(GateAction.HALT, 1, f"Bot status is {bot['status']} — no buys.")

    ticker = pick["ticker"].upper()

    # Gate 2 — strategy lock
    file_strategy = pick.get("_strategy") or pick.get("strategy")
    if file_strategy and file_strategy != bot["strategy"]:
        return GateResult(GateAction.SKIP, 2, f"Pick strategy '{file_strategy}' != bot '{bot['strategy']}'.")

    # Already holding?
    if any(t["ticker"] == ticker and t.get("status") == "open" for t in trades):
        return GateResult(GateAction.SKIP, 2, f"Already holding {ticker}.")

    fill = _fill_price(ticker, pick)
    if not fill or fill <= 0:
        return GateResult(GateAction.SKIP, 3, f"No fill price for {ticker}.")

    # Gate 3 — cash available (must afford at least 1 share)
    if cash < fill:
        return GateResult(GateAction.SKIP, 3, f"Insufficient cash ({_fmt_inr(cash)} < 1 share at {_fmt_inr(fill)}).")

    desired_inr = float(pick.get("allocation_inr") or cash)

    # Gate 4 — max capital deployed
    deployed = _deployed_capital(trades)
    max_deploy = allocation * bot["max_deployed_pct"] / 100
    room_deploy = max_deploy - deployed
    if room_deploy < fill:
        return GateResult(GateAction.SKIP, 4, f"Max deployed cap reached ({bot['max_deployed_pct']}% = {_fmt_inr(max_deploy)}).")

    # Gate 5 — per-stock cap (TRIM)
    max_per_stock = allocation * bot["max_per_stock_pct"] / 100
    existing = _position_value_for_ticker(trades, ticker)
    room_stock = max_per_stock - existing
    trimmed = False

    spend_inr = min(desired_inr, cash, room_deploy, room_stock)
    if spend_inr < fill:
        return GateResult(
            GateAction.SKIP, 5,
            f"Per-stock cap ({bot['max_per_stock_pct']}% = {_fmt_inr(max_per_stock)}) — cannot fit 1 share.",
        )
    if spend_inr < desired_inr - 0.01:
        trimmed = True

    # Gate 6 — size to whole shares
    shares = int(spend_inr // fill)
    if shares < 1:
        return GateResult(GateAction.SKIP, 6, "Not enough capital for 1 whole share after sizing.")

    cost = round(shares * fill, 2)
    if cost > cash + 0.01:
        shares = int(cash // fill)
        cost = round(shares * fill, 2)
    if shares < 1:
        return GateResult(GateAction.SKIP, 6, "Cash too tight for 1 share.")

    order = SizedOrder(
        ticker=ticker,
        shares=shares,
        fill_price=fill,
        cost=cost,
        stop_loss=float(pick["stop_loss"]),
        sell_target=float(pick["sell_target"]),
        rationale=pick.get("rationale", ""),
        trimmed=trimmed,
        source_pick=pick,
    )

    # Gate 7 — autonomy
    if _needs_approval(bot, cost):
        if skip_execute:
            return GateResult(GateAction.PENDING, 7, f"Awaiting approval ({_fmt_inr(cost)}).", order)
        pending = db.add_pending(
            bot_id,
            {
                "ticker": ticker,
                "qty": shares,
                "buyPrice": fill,
                "sellPrice": order.sell_target,
                "stopLoss": order.stop_loss,
                "cost": cost,
                "reason": order.rationale or f"Morning deploy — {ticker}",
            },
        )
        return GateResult(
            GateAction.PENDING, 7,
            f"Recorded for approval: {ticker} {shares} @ {_fmt_inr(fill)} (pending #{pending['id']}).",
            order,
        )

    if skip_execute:
        return GateResult(GateAction.EXECUTE, 8, f"Would buy {shares} × {ticker} @ {_fmt_inr(fill)}.", order)

    # Gates 8–9 — paper buy + ledger
    trade = db.execute_buy(
        bot_id,
        {
            "ticker": ticker,
            "qty": shares,
            "entry": fill,
            "ltp": fill,
            "target": order.sell_target,
            "stopLoss": order.stop_loss,
            "reasoning": order.rationale or f"Morning deploy — {ticker}",
        },
        cost,
        source="fund_manager",
    )
    ledger.append_log(
        "morning_deploy",
        f"Bought {shares} × {ticker} @ {_fmt_inr(fill)}" + (" (trimmed)" if trimmed else ""),
        order.rationale,
    )
    return GateResult(
        GateAction.EXECUTE, 9,
        f"Executed {shares} × {ticker} @ {_fmt_inr(fill)} (trade #{trade['id']}).",
        order,
    )
