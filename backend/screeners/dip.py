"""Strategy 4 — Buy the dip (Connors RSI-2 mean reversion)."""

import math

from data import fetch_history
from indicators import atr, rsi, sma
from screeners.common import DEFAULT_POOL, fetch_universe, finite_positive, format_candidate
from strategies import STRATEGY_NAMES
from universe import get_universe


def _analyze(stock: dict) -> dict | None:
    closes = stock.get("closes") or []
    highs = stock.get("highs") or []
    lows = stock.get("lows") or []
    if len(closes) < 210:
        return None

    price = finite_positive(closes[-1])
    ma200 = sma(closes, 200)
    ma5 = sma(closes, 5)
    if not price or not ma200 or not math.isfinite(ma200) or price <= ma200:
        return None

    r2 = rsi(closes, 2)
    if r2 is None or not math.isfinite(r2) or r2 >= 10:
        return None

    down_days = sum(1 for i in range(-3, 0) if closes[i] < closes[i - 1])
    score = 2
    if ma5 and math.isfinite(ma5) and price < ma5:
        score += 1
    if down_days >= 2:
        score += 1

    a = atr(highs, lows, closes, 14) or price * 0.02
    if not math.isfinite(a):
        return None
    stop = round(price - 2 * a, 2)
    sell = ma5 if ma5 and math.isfinite(ma5) and ma5 > price else price * 1.06
    sell = round(sell, 2)
    if not math.isfinite(stop) or not finite_positive(sell):
        return None

    return {
        **stock,
        "passCount": score,
        "passAll": r2 < 5,
        "signal": {"rsi2": round(r2, 1), "ma200": round(ma200, 2), "stop": stop},
        "_sell": sell,
        "_note": f"RSI(2)={r2:.1f} in uptrend (above 200 DMA) — mean-reversion bounce setup.",
    }


def screen(budget: int) -> dict:
    symbols = get_universe("dip")
    stocks, errors = fetch_universe(fetch_history, symbols, DEFAULT_POOL)

    candidates = []
    for s in stocks:
        hit = _analyze(s)
        if not hit:
            continue
        label = "Strong dip" if hit["passAll"] else "Dip signal"
        cand = format_candidate(
            hit, budget, hit["_sell"], label, hit["_note"],
            hit["passAll"],
            extra={"stopLoss": hit["signal"]["stop"]},
        )
        if cand:
            candidates.append(cand)

    candidates.sort(key=lambda c: (-c["passCount"], c["signal"].get("rsi2", 99)))
    return {
        "strategy": "dip",
        "strategyName": STRATEGY_NAMES["dip"],
        "budget": budget,
        "screenedCount": len(symbols),
        "fetchedCount": len(stocks),
        "passedCount": len(candidates),
        "affordableCount": sum(1 for c in candidates if c["canLog"]),
        "candidates": candidates[:20],
        "errors": errors,
    }
