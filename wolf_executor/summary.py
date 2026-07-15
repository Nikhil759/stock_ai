"""Build human-readable executor summaries for the Activity page."""
from __future__ import annotations


def _inr(n: float) -> str:
    return f"₹{n:,.2f}"


def build_summary(
    *,
    actions_taken: list[dict],
    cash_before: float,
    cash_after: float,
) -> str:
    parts: list[str] = []
    for a in actions_taken:
        action = a.get("action", "").upper()
        sym = a.get("symbol", "?")
        qty = a.get("quantity", 0)
        price = float(a.get("price", 0))
        value = float(a.get("value", 0))
        if action == "SELL":
            parts.append(f"Sold {sym} ({qty} @ {_inr(price)} = {_inr(value)})")
        elif action == "BUY":
            parts.append(f"Bought {sym} ({qty} @ {_inr(price)} = {_inr(value)})")
    if not parts:
        parts.append("No trades executed")
    parts.append(f"Cash: {_inr(cash_before)} → {_inr(cash_after)}")
    return ". ".join(parts) + "."
