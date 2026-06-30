"""Strategy 1 — Buy cheap quality (Graham-style fundamentals)."""

from data import fetch_stock_fundamentals
from screeners.common import DEFAULT_POOL, fetch_universe, format_candidate
from strategies import STRATEGY_NAMES
from universe import get_universe

FILTERS = [
    ("P/E ≤ 15", lambda c: c.get("pe") is not None and c["pe"] <= 15),
    ("P/B ≤ 1.5", lambda c: c.get("pb") is not None and c["pb"] <= 1.5),
    ("D/E ≤ 0.5", lambda c: c.get("de") is not None and c["de"] <= 0.5),
    ("Curr ≥ 2", lambda c: c.get("curr") is not None and c["curr"] >= 2),
    ("ROE ≥ 12%", lambda c: c.get("roe") is not None and c["roe"] >= 12),
    ("Graham ≤ 22.5", lambda c: c.get("graham") is not None and c["graham"] <= 22.5),
]


def _score(stock: dict) -> tuple[int, bool]:
    n = sum(1 for _, fn in FILTERS if fn(stock))
    return n, n == len(FILTERS)


def screen(budget: int) -> dict:
    symbols = get_universe("value")
    stocks, errors = fetch_universe(fetch_stock_fundamentals, symbols, DEFAULT_POOL)

    candidates = []
    for s in stocks:
        pass_count, pass_all = _score(s)
        if pass_count < 4:
            continue
        s = {**s, "passCount": pass_count, "passAll": pass_all}
        if pass_all:
            label, note = "Recommended", "Passes Graham-style value filters — solid long-term candidate."
        else:
            label, note = "Worth a look", f"Passes {pass_count}/6 value filters."
        cand = format_candidate(s, budget, s["fair"], label, note, pass_all)
        if cand:
            candidates.append(cand)

    candidates.sort(key=lambda c: (-c["passCount"], -float(c["upside"].strip("+%")), c["ticker"]))

    return {
        "strategy": "value",
        "strategyName": STRATEGY_NAMES["value"],
        "budget": budget,
        "screenedCount": len(symbols),
        "fetchedCount": len(stocks),
        "passedCount": sum(1 for c in candidates if c["passAll"]),
        "affordableCount": sum(1 for c in candidates if c["canLog"]),
        "candidates": candidates[:25],
        "errors": errors,
    }
