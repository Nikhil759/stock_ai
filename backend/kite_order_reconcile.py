"""Reconcile live Kite order status with Supabase trades/holdings."""
from __future__ import annotations

import logging
from typing import Any

from db import repository as repo
from wolf_executor.kite_stub import _get_kite_nonblocking

log = logging.getLogger(__name__)

_KITE_COMPLETE = frozenset({"COMPLETE"})
_KITE_PENDING = frozenset(
    {
        "OPEN",
        "TRIGGER PENDING",
        "AMO REQ RECEIVED",
        "VALIDATION PENDING",
        "PUT ORDER REQ RECEIVED",
        "MODIFY VALIDATION PENDING",
        "MODIFY AMO REQ RECEIVED",
        "OPEN PENDING",
        "OPEN QUEUED",
    }
)
_KITE_REJECTED = frozenset({"REJECTED"})
_KITE_CANCELLED = frozenset({"CANCELLED"})


def _normalize_kite_status(raw: str | None) -> str:
    return (raw or "").strip().upper()


def _fetch_kite_order(kite, order_id: str) -> dict[str, Any] | None:
    try:
        history = kite.order_history(order_id)
    except Exception as exc:
        log.warning("[KITE RECONCILE] order_history(%s) failed: %s", order_id, exc)
        return None
    if not history:
        return None
    latest = history[-1]
    return latest if isinstance(latest, dict) else None


def _refund_cash(wolf_id: str, amount: float) -> None:
    if amount <= 0:
        return
    wolf = repo.get_wolf(wolf_id)
    if not wolf:
        return
    repo.set_budget_available(
        wolf_id, float(wolf["budget_available"]) + amount
    )


def _apply_fill(
    trade: dict[str, Any],
    *,
    filled_qty: int,
    avg_price: float,
) -> None:
    wolf_id = trade["wolf_id"]
    sym = str(trade["symbol"]).upper()
    ordered_qty = int(trade["quantity"])
    limit_price = float(trade["price"])
    trade_id = int(trade["trade_id"])

    holding = next(
        (h for h in repo.list_open_holdings(wolf_id) if h["symbol"].upper() == sym),
        None,
    )
    if holding:
        repo.upsert_holding(
            wolf_id,
            sym,
            filled_qty,
            avg_price,
            sell_target=holding.get("sell_target"),
            stop_loss=holding.get("stop_loss"),
        )
    else:
        repo.upsert_holding(wolf_id, sym, filled_qty, avg_price)

    repo.update_trade_order(
        trade_id,
        order_status="complete",
        quantity=filled_qty,
        price=avg_price,
    )

    reserved = ordered_qty * limit_price
    actual = filled_qty * avg_price
    refund = round(reserved - actual, 2)
    if refund > 0.01:
        _refund_cash(wolf_id, refund)


def _apply_rejected(trade: dict[str, Any]) -> None:
    wolf_id = trade["wolf_id"]
    sym = str(trade["symbol"]).upper()
    ordered_qty = int(trade["quantity"])
    limit_price = float(trade["price"])
    trade_id = int(trade["trade_id"])

    repo.update_trade_order(trade_id, order_status="rejected")
    repo.close_holding(wolf_id, sym)
    _refund_cash(wolf_id, round(ordered_qty * limit_price, 2))


def _apply_cancelled(trade: dict[str, Any]) -> None:
    wolf_id = trade["wolf_id"]
    sym = str(trade["symbol"]).upper()
    trade_id = int(trade["trade_id"])
    ordered_qty = int(trade["quantity"])
    limit_price = float(trade["price"])

    repo.update_trade_order(trade_id, order_status="cancelled")
    repo.close_holding(wolf_id, sym)
    _refund_cash(wolf_id, round(ordered_qty * limit_price, 2))


def reconcile_live_orders_for_wolf(wolf_id: str) -> dict[str, Any]:
    """Poll Zerodha for live BUY order status; update trades/holdings/cash."""
    wolf = repo.get_wolf(wolf_id)
    if not wolf or str(wolf.get("mode") or "paper") != "live":
        return {"skipped": True, "reason": "not_live"}

    trades = repo.list_reconcilable_live_buys(wolf_id)
    pending_trades = [t for t in trades if t.get("order_status") == "pending"]
    if not pending_trades:
        return {
            "filled": [],
            "still_pending": [],
            "rejected": [],
            "cancelled": [],
        }

    # Read-only order_history — works on Railway when today's token is synced from Mac.
    kite = _get_kite_nonblocking()
    if kite is None:
        return {
            "skipped": True,
            "reason": "no_kite_session",
            "still_pending": [
                {"ticker": t["symbol"], "kite_order_id": t.get("kite_order_id")}
                for t in pending_trades
            ],
        }

    filled: list[dict[str, Any]] = []
    still_pending: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []

    for trade in pending_trades:
        sym = str(trade["symbol"]).upper()
        oid = str(trade.get("kite_order_id") or "")
        if not oid:
            continue

        order = _fetch_kite_order(kite, oid)
        if not order:
            still_pending.append({"ticker": sym, "kite_order_id": oid})
            continue

        status = _normalize_kite_status(order.get("status"))
        filled_qty = int(order.get("filled_quantity") or 0)
        avg_price = float(order.get("average_price") or trade["price"] or 0)

        if status in _KITE_COMPLETE or (
            status not in _KITE_PENDING
            and status not in _KITE_REJECTED
            and status not in _KITE_CANCELLED
            and filled_qty > 0
        ):
            if filled_qty <= 0:
                filled_qty = int(trade["quantity"])
            if avg_price <= 0:
                avg_price = float(trade["price"])
            _apply_fill(trade, filled_qty=filled_qty, avg_price=avg_price)
            filled.append(
                {
                    "ticker": sym,
                    "quantity": filled_qty,
                    "price": avg_price,
                    "kite_order_id": oid,
                }
            )
            log.info(
                "[KITE RECONCILE] %s %s filled qty=%d @ %.2f",
                wolf_id,
                sym,
                filled_qty,
                avg_price,
            )
        elif status in _KITE_REJECTED:
            _apply_rejected(trade)
            rejected.append({"ticker": sym, "kite_order_id": oid})
        elif status in _KITE_CANCELLED:
            _apply_cancelled(trade)
            cancelled.append({"ticker": sym, "kite_order_id": oid})
        elif status in _KITE_PENDING:
            still_pending.append({"ticker": sym, "kite_order_id": oid})
        else:
            log.warning(
                "[KITE RECONCILE] unknown status %r for %s order %s",
                status,
                sym,
                oid,
            )
            still_pending.append({"ticker": sym, "kite_order_id": oid, "status": status})

    return {
        "filled": filled,
        "still_pending": still_pending,
        "rejected": rejected,
        "cancelled": cancelled,
    }
