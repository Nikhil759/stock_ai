"""Shared screener helpers."""

from __future__ import annotations

import math

from throttle import ThrottledPool


def fmt_inr(n: float) -> str:
    return "₹" + f"{round(n):,}"


def finite_positive(n) -> float | None:
    """Return n if a finite number > 0, else None."""
    if n is None:
        return None
    try:
        f = float(n)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def size_for_budget(price: float, budget: int) -> tuple[int, float]:
    p = finite_positive(price)
    if not p:
        return 0, 0.0
    shares = int(budget // p)
    cost = shares * p
    return shares, cost


def format_candidate(
    stock: dict,
    budget: int,
    sell_price: float,
    rec_label: str,
    rec_note: str,
    rec_good: bool = True,
    extra: dict | None = None,
) -> dict | None:
    price = finite_positive(stock.get("price"))
    sell = finite_positive(sell_price)
    if not price or not sell:
        return None

    shares, cost = size_for_budget(price, budget)
    upside_pct = round((sell - price) / price * 100)
    c = {
        "ticker": stock["ticker"],
        "name": stock.get("name", stock["ticker"]),
        "sector": stock.get("sector", "—"),
        "buyPrice": price,
        "sellPrice": round(sell, 2),
        "buyFmt": f"₹{price:,.2f}",
        "sellFmt": f"₹{round(sell):,.0f}",
        "passCount": stock.get("passCount", 0),
        "passAll": stock.get("passAll", rec_good),
        "recLabel": rec_label,
        "recGood": rec_good,
        "recNote": rec_note,
        "canLog": shares >= 1,
        "shares": shares,
        "cost": cost,
        "costFmt": fmt_inr(cost),
        "leftFmt": fmt_inr(budget - cost),
        "upside": f"+{max(upside_pct, 0)}%",
        "gainNote": f"~{upside_pct}% to sell target",
        "signal": stock.get("signal", {}),
    }
    if extra:
        c.update(extra)
    return c


DEFAULT_POOL = ThrottledPool(max_workers=6, delay_sec=0.22, retries=2)


def fetch_universe(fn, symbols: list[str], pool: ThrottledPool | None = None) -> tuple[list[dict], list[str]]:
    p = pool or DEFAULT_POOL
    return p.map(fn, symbols)
