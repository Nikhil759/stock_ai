"""Strategy 3 — Darvas box breakout."""

from data import fetch_history, market_above_200dma
from screeners.common import DEFAULT_POOL, fetch_universe, format_candidate
from strategies import STRATEGY_NAMES
from universe import get_universe


def _analyze(stock: dict) -> dict | None:
    highs = stock.get("highs") or []
    lows = stock.get("lows") or []
    closes = stock.get("closes") or []
    vols = stock.get("volumes") or []
    if len(closes) < 25:
        return None

    window = 20
    box_high = max(highs[-window - 1 : -1])
    box_low = min(lows[-window - 1 : -1])
    mid = (box_high + box_low) / 2
    if mid <= 0:
        return None
    width_pct = (box_high - box_low) / mid * 100
    if width_pct > 12 or width_pct < 2:
        return None

    price = closes[-1]
    vol_avg = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 0
    vol_ok = vol_avg > 0 and vols[-1] >= vol_avg * 1.25
    breakout = price > box_high * 1.002

    if not breakout or not vol_ok:
        return None

    stop = round(box_low * 0.99, 2)
    sell = round(price + (price - stop) * 2, 2)  # 2R target placeholder for UI
    return {
        **stock,
        "passCount": 3,
        "passAll": True,
        "signal": {
            "boxHigh": round(box_high, 2),
            "boxLow": round(box_low, 2),
            "widthPct": round(width_pct, 1),
            "stop": stop,
        },
        "_sell": sell,
        "_note": f"Box breakout — range ₹{box_low:,.0f}–₹{box_high:,.0f}, stop below box at ₹{stop:,.0f}.",
    }


def screen(budget: int) -> dict:
    symbols = get_universe("box")
    market_ok = market_above_200dma()
    stocks, errors = fetch_universe(fetch_history, symbols, DEFAULT_POOL)

    candidates = []
    for s in stocks:
        hit = _analyze(s)
        if not hit:
            continue
        if not market_ok:
            continue
        cand = format_candidate(
                hit, budget, hit["_sell"],
                "Box breakout",
                hit["_note"],
                True,
                extra={"stopLoss": hit["signal"]["stop"]},
            )
        if cand:
            candidates.append(cand)

    candidates.sort(key=lambda c: -float(c["upside"].strip("+%")))
    return {
        "strategy": "box",
        "strategyName": STRATEGY_NAMES["box"],
        "budget": budget,
        "screenedCount": len(symbols),
        "fetchedCount": len(stocks),
        "passedCount": len(candidates),
        "affordableCount": sum(1 for c in candidates if c["canLog"]),
        "candidates": candidates[:15],
        "errors": errors,
        "marketFilter": "Nifty above 200 DMA" if market_ok else "No new box entries — market weak",
    }
