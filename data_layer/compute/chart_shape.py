"""
Phase 1 — translate the chart's SHAPE into plain language the LLM can use.
Computed in Python from the same bars; the LLM never sees raw arrays.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from ..dossier import ChartShape
from .bars import to_frame


def _slope_label(series: pd.Series, n: int) -> str:
    if len(series) < n:
        return "unknown"
    seg = series.tail(n).reset_index(drop=True)
    x = np.arange(len(seg))
    # normalized slope: % change per bar relative to mean level
    slope = np.polyfit(x, seg.values, 1)[0]
    level = seg.mean()
    if not level:
        return "flat"
    pct_per_bar = slope / level * 100
    if pct_per_bar > 0.08:
        return "rising"
    if pct_per_bar < -0.08:
        return "falling"
    return "flat"


def compute_chart_shape(bars) -> ChartShape:
    df = to_frame(bars)
    if df.empty or df["close"].dropna().empty:
        return ChartShape()
    close = df["close"].dropna().reset_index(drop=True)
    price = float(close.iloc[-1])

    trend_50 = _slope_label(close, 50)
    trend_200 = _slope_label(close, 200)

    # consolidation: width of the recent 20-day range as % of its midpoint
    consolidation = None
    box_width_pct = None
    breakout_above_box = None
    if len(close) >= 20:
        seg = close.tail(20)
        hi, lo = float(seg.max()), float(seg.min())
        mid = (hi + lo) / 2
        if mid:
            width = (hi - lo) / mid * 100
            box_width_pct = round(width, 1)
            tag = "tight" if width <= 12 else "loose"
            consolidation = f"{tag} range, ~{width:.0f}% wide, 20 days"

    # Darvas box breakout: prior 20 sessions' range (excl. today) vs today's close
    if (
        len(df) >= 25
        and "high" in df
        and "low" in df
        and df["high"].notna().sum() >= 21
        and df["low"].notna().sum() >= 21
    ):
        prior = df.iloc[-21:-1]
        box_high = float(prior["high"].max())
        box_low = float(prior["low"].min())
        box_mid = (box_high + box_low) / 2
        if box_mid > 0:
            w = (box_high - box_low) / box_mid * 100
            if box_width_pct is None:
                box_width_pct = round(w, 1)
            if 2 <= w <= 12:
                breakout_above_box = price > box_high * 1.002

    # volume pattern over last ~10 sessions
    volume_pattern = "unknown"
    if "volume" in df and df["volume"].notna().sum() >= 20:
        v = df["volume"].dropna()
        recent = v.tail(5).mean()
        base = v.tail(20).mean()
        if base:
            if recent > base * 1.3:
                volume_pattern = "rising, possible breakout surge"
            elif recent < base * 0.7:
                volume_pattern = "drying up"
            else:
                volume_pattern = "flat, no breakout surge"

    # where price sits in its 52w range
    distance_note = None
    window = close.tail(252)
    hi, lo = float(window.max()), float(window.min())
    if hi > lo:
        pos = (price - lo) / (hi - lo)
        if pos >= 0.85:
            distance_note = "near the top of its 52w range"
        elif pos <= 0.15:
            distance_note = "near the bottom of its 52w range"
        else:
            distance_note = "sits mid-range between 52w high and low"

    return ChartShape(
        trend_50d=trend_50,
        trend_200d=trend_200,
        consolidation=consolidation,
        volume_pattern=volume_pattern,
        distance_note=distance_note,
        box_width_pct=box_width_pct,
        breakout_above_box=breakout_above_box,
    )
