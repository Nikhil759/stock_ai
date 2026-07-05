"""Circuit breaker — daily loss limit enforcement."""

from __future__ import annotations

from datetime import date

import database as db
from fund_manager.ledger import BotLedger


def ensure_day_baseline(bot_id: int) -> None:
    """Set today's portfolio baseline if missing or stale (new trading day)."""
    bot = db.get_bot(bot_id)
    if not bot:
        return
    today = date.today().isoformat()
    if bot.get("dayStartDate") == today and bot.get("dayStartPortfolioValue") is not None:
        return
    ledger = BotLedger(bot_id)
    value = ledger.portfolio_value()
    db.set_day_baseline(bot_id, value, today)


def daily_loss_pct(bot_id: int) -> float:
    """Negative P&L today as % of allocation (0 if flat or up)."""
    bot = db.get_bot(bot_id)
    if not bot:
        return 0.0
    baseline = bot.get("dayStartPortfolioValue")
    if baseline is None:
        return 0.0
    ledger = BotLedger(bot_id)
    current = ledger.portfolio_value()
    loss = baseline - current
    if loss <= 0:
        return 0.0
    alloc = bot["allocation"]
    if alloc <= 0:
        return 0.0
    return round(loss / alloc * 100, 2)


def check_and_trip_breaker(bot_id: int) -> bool:
    """Trip breaker if daily loss exceeds max_daily_loss_pct. Returns True if tripped."""
    bot = db.get_bot(bot_id)
    if not bot:
        return False
    ensure_day_baseline(bot_id)
    loss_pct = daily_loss_pct(bot_id)
    limit = bot.get("max_daily_loss_pct", 5)
    if loss_pct < limit:
        return bool(bot.get("breakerTripped"))

    if not bot.get("breakerTripped"):
        db.set_breaker_tripped(bot_id, True)
        ledger = BotLedger(bot_id)
        ledger.append_log(
            "breaker_tripped",
            f"Daily loss {loss_pct:.1f}% ≥ limit {limit}%",
            "All new buys halted until breaker clears.",
        )
    return True


def pre_open_reset(bot_id: int) -> None:
    """Auto-clear breaker and refresh day baseline at morning pre-open."""
    db.clear_breaker_if_auto(bot_id)
    ensure_day_baseline(bot_id)
