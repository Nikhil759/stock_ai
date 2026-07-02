"""
Phase 1 — compute the technicals block from bars you already fetch.
No new data source. Every number the LLM will reason over is produced here.

All functions degrade gracefully: not enough history -> the field is None,
never a crash.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd

from ..dossier import Technicals
from .bars import to_frame


def _sma(s: pd.Series, n: int) -> Optional[float]:
    if len(s) < n:
        return None
    return float(s.tail(n).mean())


def _rsi(close: pd.Series, period: int) -> Optional[float]:
    """Wilder's RSI. Works for RSI(2) and RSI(14)."""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100 - (100 / (1 + rs)))


def _atr_pct(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(df) < period + 1:
        return None
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    price = close.iloc[-1]
    if pd.isna(atr) or not price:
        return None
    return round(float(atr / price * 100), 2)


def _return_pct(close: pd.Series, lookback: int) -> Optional[float]:
    if len(close) < lookback + 1:
        return None
    past = close.iloc[-(lookback + 1)]
    now = close.iloc[-1]
    if not past:
        return None
    return round(float((now / past - 1) * 100), 2)


def compute_technicals(bars, nifty_closes=None) -> Technicals:
    df = to_frame(bars)
    if df.empty or df["close"].dropna().empty:
        return Technicals()
    close = df["close"].dropna().reset_index(drop=True)
    price = float(close.iloc[-1])

    dma_50 = _sma(close, 50)
    dma_200 = _sma(close, 200)

    # 52-week extremes (use up to last 252 sessions)
    window = close.tail(252)
    hi = float(window.max())
    lo = float(window.min())
    pct_from_high = round((price / hi - 1) * 100, 2) if hi else None
    pct_from_low = round((price / lo - 1) * 100, 2) if lo else None

    # volume vs 20d average
    vol_ratio = None
    if "volume" in df and df["volume"].notna().sum() >= 20:
        v = df["volume"].dropna()
        avg20 = v.tail(20).mean()
        if avg20:
            vol_ratio = round(float(v.iloc[-1] / avg20), 2)

    # relative strength vs Nifty = stock return minus index return (pp)
    rs3 = rs6 = None
    r3 = _return_pct(close, 63)
    r6 = _return_pct(close, 126)
    if nifty_closes is not None:
        ns = pd.Series(nifty_closes).dropna().reset_index(drop=True)
        n3 = _return_pct(ns, 63)
        n6 = _return_pct(ns, 126)
        if r3 is not None and n3 is not None:
            rs3 = round(r3 - n3, 2)
        if r6 is not None and n6 is not None:
            rs6 = round(r6 - n6, 2)

    return Technicals(
        dma_50=round(dma_50, 2) if dma_50 else None,
        dma_200=round(dma_200, 2) if dma_200 else None,
        above_50dma=(price > dma_50) if dma_50 else None,
        above_200dma=(price > dma_200) if dma_200 else None,
        rsi_2=round(_rsi(close, 2), 1) if _rsi(close, 2) is not None else None,
        rsi_14=round(_rsi(close, 14), 1) if _rsi(close, 14) is not None else None,
        pct_from_52w_high=pct_from_high,
        pct_from_52w_low=pct_from_low,
        volume_vs_20d_avg=vol_ratio,
        atr_pct=_atr_pct(df),
        rel_strength_vs_nifty_3m=rs3,
        rel_strength_vs_nifty_6m=rs6,
        return_1m=_return_pct(close, 21),
        return_3m=r3,
        return_6m=r6,
    )
