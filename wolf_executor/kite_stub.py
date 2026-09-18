"""Live Zerodha order placement via Kite Connect."""
from __future__ import annotations

import logging
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)


def live_orders_enabled() -> bool:
    """Explicit opt-in via env (Mac launchd jobs set this)."""
    return os.getenv("WOLF_LIVE_ORDERS_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _get_kite_nonblocking():
    try:
        from fund_manager.kite_auth import get_kite_nonblocking
    except ImportError:
        try:
            from backend.fund_manager.kite_auth import get_kite_nonblocking
        except ImportError:
            return None
    try:
        return get_kite_nonblocking()
    except Exception:
        return None


def live_orders_allowed() -> bool:
    """True when this host may place real Kite orders now."""
    if live_orders_enabled():
        return True
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        return False
    return _get_kite_nonblocking() is not None


def kite_order_variety(*, now: datetime | None = None) -> str:
    """Return ``regular`` during NSE cash session, else ``amo``."""
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return "amo"
    t = dt.time()
    if _MARKET_OPEN <= t <= _MARKET_CLOSE:
        return "regular"
    return "amo"


def _get_kite():
    try:
        from fund_manager.kite_auth import get_kite
    except ImportError:
        from backend.fund_manager.kite_auth import get_kite
    return get_kite()


def place_kite_order(
    *,
    wolf_id: str,
    symbol: str,
    action: str,
    quantity: int,
    price: float,
) -> str:
    """Place a LIMIT CNC order on NSE. Returns the Kite ``order_id``."""
    if not live_orders_allowed():
        raise RuntimeError(
            "Live Kite orders are disabled on this host "
            "(Railway cannot place orders; on Mac sync today's Kite token or set "
            "WOLF_LIVE_ORDERS_ENABLED=1)."
        )

    side = action.strip().upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got {action!r}")

    qty = int(quantity)
    if qty <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")

    limit_price = round(float(price), 2)
    if limit_price <= 0:
        raise ValueError(f"price must be positive, got {price}")

    sym = symbol.strip().upper().split(":")[-1]
    variety = kite_order_variety()
    tag = f"wolf_{wolf_id}"[:20]

    kite = _get_kite()
    txn = (
        kite.TRANSACTION_TYPE_BUY
        if side == "BUY"
        else kite.TRANSACTION_TYPE_SELL
    )
    var = kite.VARIETY_REGULAR if variety == "regular" else kite.VARIETY_AMO

    log.info(
        "[KITE] %s %s qty=%d @ %.2f variety=%s wolf=%s",
        side,
        sym,
        qty,
        limit_price,
        variety,
        wolf_id,
    )

    try:
        order_id = kite.place_order(
            variety=var,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=sym,
            transaction_type=txn,
            quantity=qty,
            product=kite.PRODUCT_CNC,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            tag=tag,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Kite order failed ({side} {qty} {sym} @ {limit_price}): {exc}"
        ) from exc

    order_id_str = str(order_id)
    log.info("[KITE] order placed id=%s wolf=%s", order_id_str, wolf_id)
    return order_id_str
