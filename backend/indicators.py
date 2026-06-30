"""Technical indicators from daily OHLCV bars."""

from __future__ import annotations


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: list[float], period: int = 2) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else None


def pct_from_high(closes: list[float], lookback: int = 252) -> float | None:
    if not closes:
        return None
    window = closes[-lookback:] if len(closes) >= lookback else closes
    hi = max(window)
    if hi <= 0:
        return None
    return (hi - closes[-1]) / hi * 100


def relative_strength_score(closes: list[float], index_closes: list[float], lookback: int = 126) -> float | None:
    """Simple RS: stock return minus index return over lookback."""
    if len(closes) < lookback + 1 or len(index_closes) < lookback + 1:
        return None
    s_ret = (closes[-1] / closes[-lookback] - 1) * 100
    i_ret = (index_closes[-1] / index_closes[-lookback] - 1) * 100
    return s_ret - i_ret


def detect_box(highs: list[float], lows: list[float], closes: list[float], window: int = 20) -> dict | None:
    """Darvas-style box over last `window` sessions (excluding today)."""
    if len(closes) < window + 1:
        return None
    h = highs[-window - 1 : -1]
    l = lows[-window - 1 : -1]
    if not h or not l:
        return None
    box_high = max(h)
    box_low = min(l)
    mid = (box_high + box_low) / 2
    if mid <= 0:
        return None
    width_pct = (box_high - box_low) / mid * 100
    if width_pct > 12 or width_pct < 1.5:
        return None
    return {"boxHigh": box_high, "boxLow": box_low, "widthPct": round(width_pct, 2)}


def detect_base(highs: list[float], lows: list[float], window: int = 35) -> dict | None:
    if len(highs) < window:
        return None
    h = highs[-window:]
    l = lows[-window:]
    base_high = max(h)
    base_low = min(l)
    mid = (base_high + base_low) / 2
    if mid <= 0:
        return None
    width_pct = (base_high - base_low) / mid * 100
    if width_pct > 18:
        return None
    return {"baseHigh": base_high, "baseLow": base_low, "widthPct": round(width_pct, 2)}
