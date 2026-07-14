"""
Technicals block from price bars.

Phase B engine: the `ta` library (pure Python, Python 3.14–compatible).
Same dossier field names/shapes as before — internal engine swap only.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, MACD, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from ..dossier import Technicals
from .bars import to_frame

_ENGINE_LOGGED = False
_ENGINE_NAME = "ta"


def _last(series: pd.Series | None) -> Optional[float]:
    if series is None or series.empty:
        return None
    v = series.iloc[-1]
    if pd.isna(v):
        return None
    return float(v)


def _return_pct(close: pd.Series, lookback: int) -> Optional[float]:
    if len(close) < lookback + 1:
        return None
    past = close.iloc[-(lookback + 1)]
    now = close.iloc[-1]
    if not past:
        return None
    return round(float((now / past - 1) * 100), 2)


def _log_engine_once() -> None:
    global _ENGINE_LOGGED
    if not _ENGINE_LOGGED:
        print(f"[TECHNICALS] engine={_ENGINE_NAME}")
        _ENGINE_LOGGED = True


def _compute_via_ta(df: pd.DataFrame) -> dict:
    """Map `ta` library indicators → dossier technicals keys."""
    close, high, low = df["close"], df["high"], df["low"]
    out: dict = {}

    out["rsi_14"] = _last(RSIIndicator(close=close, window=14).rsi())
    out["rsi_2"] = _last(RSIIndicator(close=close, window=2).rsi())

    for n, key in ((20, "dma_20"), (50, "dma_50"), (200, "dma_200")):
        out[key] = _last(SMAIndicator(close=close, window=n).sma_indicator())

    atr_v = _last(
        AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    )
    out["atr"] = round(atr_v, 4) if atr_v is not None else None
    price = float(close.iloc[-1])
    if atr_v is not None and price:
        out["atr_pct"] = round(atr_v / price * 100, 2)

    macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_v = _last(macd_ind.macd())
    sig_v = _last(macd_ind.macd_signal())
    hist_v = _last(macd_ind.macd_diff())
    out["macd"] = round(macd_v, 4) if macd_v is not None else None
    out["macd_signal"] = round(sig_v, 4) if sig_v is not None else None
    out["macd_hist"] = round(hist_v, 4) if hist_v is not None else None

    adx_v = _last(ADXIndicator(high=high, low=low, close=close, window=14).adx())
    out["adx"] = round(adx_v, 2) if adx_v is not None else None

    bb = BollingerBands(close=close, window=20, window_dev=2)
    bu = _last(bb.bollinger_hband())
    bm = _last(bb.bollinger_mavg())
    bl = _last(bb.bollinger_lband())
    out["bb_upper"] = round(bu, 2) if bu is not None else None
    out["bb_middle"] = round(bm, 2) if bm is not None else None
    out["bb_lower"] = round(bl, 2) if bl is not None else None

    return out


def compute_technicals(bars, nifty_closes=None, ticker: str = "") -> Technicals:
    _log_engine_once()

    df = to_frame(bars)
    if df.empty or df["close"].dropna().empty:
        return Technicals()

    df = df.dropna(subset=["close"]).reset_index(drop=True)
    close = df["close"]
    price = float(close.iloc[-1])

    vals = _compute_via_ta(df)

    dma_20 = vals.get("dma_20")
    dma_50 = vals.get("dma_50")
    dma_200 = vals.get("dma_200")
    rsi_14 = vals.get("rsi_14")
    rsi_2 = vals.get("rsi_2")
    atr = vals.get("atr")
    atr_pct = vals.get("atr_pct")

    window = close.tail(252)
    hi = float(window.max())
    lo = float(window.min())
    pct_from_high = round((price / hi - 1) * 100, 2) if hi else None
    pct_from_low = round((price / lo - 1) * 100, 2) if lo else None

    vol_ratio = None
    if "volume" in df and df["volume"].notna().sum() >= 20:
        v = df["volume"].dropna()
        avg20 = v.tail(20).mean()
        if avg20:
            vol_ratio = round(float(max(0.0, v.iloc[-1] / avg20)), 2)

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

    t = Technicals(
        dma_20=round(dma_20, 2) if dma_20 is not None else None,
        dma_50=round(dma_50, 2) if dma_50 is not None else None,
        dma_200=round(dma_200, 2) if dma_200 is not None else None,
        above_50dma=(price > dma_50) if dma_50 is not None else None,
        above_200dma=(price > dma_200) if dma_200 is not None else None,
        rsi_2=round(rsi_2, 1) if rsi_2 is not None else None,
        rsi_14=round(rsi_14, 1) if rsi_14 is not None else None,
        pct_from_52w_high=pct_from_high,
        pct_from_52w_low=pct_from_low,
        volume_vs_20d_avg=vol_ratio,
        atr=round(atr, 4) if atr is not None else None,
        atr_pct=atr_pct,
        rel_strength_vs_nifty_3m=rs3,
        rel_strength_vs_nifty_6m=rs6,
        return_1m=_return_pct(close, 21),
        return_3m=r3,
        return_6m=r6,
        macd=vals.get("macd"),
        macd_signal=vals.get("macd_signal"),
        macd_hist=vals.get("macd_hist"),
        adx=vals.get("adx"),
        bb_upper=vals.get("bb_upper"),
        bb_middle=vals.get("bb_middle"),
        bb_lower=vals.get("bb_lower"),
    )

    if ticker:
        print(
            f"[TECHNICALS] {ticker} indicators — "
            f"rsi14={t.rsi_14} macd={t.macd} adx={t.adx} atr={t.atr} "
            f"dma20={t.dma_20} dma50={t.dma_50} dma200={t.dma_200}"
        )
    return t
