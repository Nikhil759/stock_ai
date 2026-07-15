"""Live order placement stub — not implemented in Phase 2."""
from __future__ import annotations


def place_kite_order(
    *,
    wolf_id: str,
    symbol: str,
    action: str,
    quantity: int,
    price: float,
) -> None:
    raise NotImplementedError(
        f"Live Zerodha execution is not implemented (wolf={wolf_id}, "
        f"{action} {quantity} {symbol} @ {price}). Use mode='paper'."
    )
