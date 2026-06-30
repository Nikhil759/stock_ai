"""Strategy 2 — Buy the winners (simplified CANSLIM + breakout)."""

from data import fetch_nifty_index_history, fetch_stock_full, market_above_200dma
from indicators import detect_base, pct_from_high, relative_strength_score, sma
from screeners.common import DEFAULT_POOL, fetch_universe, format_candidate
from strategies import STRATEGY_NAMES
from universe import get_universe


def _score_stock(stock: dict, index_closes: list[float]) -> tuple[int, dict]:
    closes = stock.get("closes") or []
    highs = stock.get("highs") or []
    lows = stock.get("lows") or []
    vols = stock.get("volumes") or []
    price = stock["price"]
    signals = {}
    score = 0

    if len(closes) < 200:
        return 0, signals

    ma50 = sma(closes, 50)
    ma200 = sma(closes, 200)
    if ma50 and ma200 and price > ma50 > ma200:
        score += 1
        signals["trend"] = "Above rising 50/200 DMA"

    dist_hi = pct_from_high(closes, 252)
    if dist_hi is not None and dist_hi <= 15:
        score += 1
        signals["nearHigh"] = f"Within {dist_hi:.1f}% of 52w high"

    rs = relative_strength_score(closes, index_closes) if index_closes else None
    if rs is not None and rs > 0:
        score += 1
        signals["rs"] = f"RS vs Nifty +{rs:.1f}%"

    if stock.get("roe") and stock["roe"] >= 12:
        score += 1
    if stock.get("pe") and 5 < stock["pe"] < 60:
        score += 1

    base = detect_base(highs, lows, window=35)
    vol_avg = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 0
    vol_today = vols[-1] if vols else 0
    breakout = base and price > base["baseHigh"] * 1.005 and vol_avg > 0 and vol_today >= vol_avg * 1.3
    if breakout:
        score += 2
        signals["breakout"] = "Volume breakout above base"
    elif base:
        score += 1
        signals["base"] = f"Tight base ({base['widthPct']}% range)"

    return score, signals


def screen(budget: int) -> dict:
    symbols = get_universe("winners")
    market_ok = market_above_200dma()
    idx = fetch_nifty_index_history() or {"closes": []}
    stocks, errors = fetch_universe(fetch_stock_full, symbols, DEFAULT_POOL)

    candidates = []
    for s in stocks:
        score, signals = _score_stock(s, idx["closes"])
        if score < 4:
            continue
        if not market_ok and score < 5:
            continue
        price = s["price"]
        sell = round(price * 1.22, 2)
        note = " · ".join(signals.values()) if signals else "Momentum + quality screen match."
        if not market_ok:
            note = "Market below 200 DMA — higher bar applied. " + note
        s = {**s, "passCount": score, "passAll": score >= 6, "signal": signals}
        cand = format_candidate(
                s, budget, sell,
                "Breakout watch" if signals.get("breakout") else "Winner candidate",
                note,
                score >= 5,
            )
        if cand:
            candidates.append(cand)

    candidates.sort(key=lambda c: (-c["passCount"], c["ticker"]))
    return {
        "strategy": "winners",
        "strategyName": STRATEGY_NAMES["winners"],
        "budget": budget,
        "screenedCount": len(symbols),
        "fetchedCount": len(stocks),
        "passedCount": len(candidates),
        "affordableCount": sum(1 for c in candidates if c["canLog"]),
        "candidates": candidates[:20],
        "errors": errors,
        "marketFilter": "Nifty above 200 DMA" if market_ok else "Nifty below 200 DMA — cautious",
    }
