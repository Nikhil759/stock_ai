"""End-of-day job — refresh prices, auto-exit, return cash to pool."""

import logging

import database as db
from data import fetch_latest_price

log = logging.getLogger(__name__)


def _refresh_open_trades(bot_id: int, bot: dict, *, log_action: str) -> dict:
    open_trades = db.get_trades(bot_id, status="open")
    closed = []
    updated = []
    failed = []
    check_exits = bot["status"] == "running"

    for t in open_trades:
        price = fetch_latest_price(t["ticker"])
        if price is None:
            failed.append(t["ticker"])
            continue
        db.update_trade_ltp(t["id"], price)

        exit_reason = None
        if check_exits:
            if price >= t["target"]:
                exit_reason = "target"
            elif price <= t["stopLoss"]:
                exit_reason = "stop_loss"

        if exit_reason:
            result = db.close_trade(t["id"], price, exit_reason)
            if result:
                proceeds = t["qty"] * price
                closed.append({
                    "ticker": t["ticker"],
                    "price": price,
                    "reason": exit_reason,
                    "proceeds": proceeds,
                })
                _maybe_redeploy_freed_cash(bot_id, proceeds, t["ticker"])
        else:
            updated.append({"ticker": t["ticker"], "ltp": price})

    bot_after = db.get_bot(bot_id)
    detail = f"Checked {len(open_trades)} positions · {len(closed)} sold · {len(updated)} updated"
    if failed:
        detail += f" · {len(failed)} failed"
    db.log_action(
        bot_id,
        log_action,
        detail,
        f"Portfolio value ₹{bot_after['portfolioValue']:,.0f}",
    )

    return {
        "botId": bot_id,
        "checked": len(open_trades),
        "closed": closed,
        "updated": updated,
        "failed": failed,
        "availableCash": bot_after["availableCash"],
        "portfolioValue": bot_after["portfolioValue"],
    }


def _maybe_redeploy_freed_cash(bot_id: int, proceeds: float, ticker: str) -> None:
    """Optional mid-day redeploy when refresh closes a position."""
    try:
        from fund_manager.redeploy import handle_freed_cash
        handle_freed_cash(bot_id, proceeds, ticker)
    except Exception:
        log.exception("Mid-day redeploy failed for bot %s %s", bot_id, ticker)


def refresh_prices(bot_id: int) -> dict:
    """Manual refresh — update LTP; auto-exit only when bot is running."""
    bot = db.get_bot(bot_id)
    if not bot:
        return {"error": "Wolf not found"}
    if bot["status"] == "terminated":
        return {"skipped": True, "reason": "terminated"}
    if not db.get_trades(bot_id, status="open"):
        return {"botId": bot_id, "checked": 0, "closed": [], "updated": [], "message": "No open positions"}

    return _refresh_open_trades(bot_id, bot, log_action="prices_refreshed")


def run_eod(bot_id: int) -> dict:
    bot = db.get_bot(bot_id)
    if not bot:
        return {"error": "Wolf not found"}

    if bot["status"] == "terminated":
        return {"skipped": True, "reason": "terminated"}

    if bot["status"] == "paused":
        return {"skipped": True, "reason": "paused", "message": "Paused — no price checks or exits."}

    open_trades = db.get_trades(bot_id, status="open")
    if not open_trades:
        return {"botId": bot_id, "checked": 0, "closed": [], "updated": []}

    result = _refresh_open_trades(bot_id, bot, log_action="eod_run")
    bot_after = db.get_bot(bot_id)
    return {
        **result,
        "availableCash": bot_after["availableCash"],
        "portfolioValue": bot_after["portfolioValue"],
    }
