"""
Pure-pandas indicator helpers (pandas-ta compatible outputs).

Used when pandas-ta cannot be imported (e.g. Python 3.14 / missing numba).
Formulas follow common Wilder / EMA conventions used by pandas-ta.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def _last(s: pd.Series | None) -> Optional[float]:
    if s is None or s.empty:
        return None
    v = s.iloc[-1]
    if pd.isna(v):
        return None
    return float(v)


def sma(close: pd.Series, length: int) -> pd.Series:
    return close.rolling(length, min_periods=length).mean()


def ema(close: pd.Series, length: int) -> pd.Series:
    return close.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    out = out.where(avg_loss != 0, 100.0)
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame(
        {
            f"MACD_{fast}_{slow}_{signal}": macd_line,
            f"MACDs_{fast}_{slow}_{signal}": signal_line,
            f"MACDh_{fast}_{slow}_{signal}": hist,
        }
    )


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.DataFrame:
    """Wilder ADX. Returns DataFrame with ADX_{length} column."""
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = atr(high, low, close, length=1)  # true range series before smooth
    # Proper TR:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr_n = tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    plus_di = 100 * (
        plus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr_n
    )
    minus_di = 100 * (
        minus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr_n
    )
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)).fillna(0)
    adx_s = dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    return pd.DataFrame({f"ADX_{length}": adx_s})


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, length)
    dev = close.rolling(length, min_periods=length).std(ddof=0)
    upper = mid + std * dev
    lower = mid - std * dev
    return pd.DataFrame(
        {
            f"BBU_{length}_{std}": upper,
            f"BBM_{length}_{std}": mid,
            f"BBL_{length}_{std}": lower,
        }
    )


def last_sma(close: pd.Series, length: int) -> Optional[float]:
    return _last(sma(close, length))


def last_rsi(close: pd.Series, length: int) -> Optional[float]:
    return _last(rsi(close, length))


def last_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14
) -> Optional[float]:
    return _last(atr(high, low, close, length))
