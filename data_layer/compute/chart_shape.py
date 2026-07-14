"""
Chart SHAPE into plain language + Phase B PKScreener-inspired signals.

Existing Phase 1 fields (trend labels, consolidation prose, box breakout) are
kept. Phase B adds consolidation_percentage, volume_ratio, Weinstein stage,
and named pattern flags — computation only; no strategy funnel changes.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..dossier import ChartShape
from .bars import to_frame

_LOOKBACK = 22
_CONSOL_TIGHT_PCT = 10.0
_VOL_CONFIRM = 2.5
_SMA_150 = 150
_SMA_SLOPE_LOOKBACK = 20
_STAGE2_ABOVE_LOW_PCT = 25.0


def _slope_label(series: pd.Series, n: int) -> str:
    if len(series) < n:
        return "unknown"
    seg = series.tail(n).reset_index(drop=True)
    x = np.arange(len(seg))
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


def _consolidation_pct(df: pd.DataFrame, lookback: int = _LOOKBACK) -> Optional[float]:
    """(highest_high - lowest_low) / lowest_low * 100 over lookback sessions."""
    need = ["high", "low"]
    if any(c not in df for c in need) or len(df) < lookback:
        return None
    window = df.tail(lookback)
    hh = float(window["high"].max())
    ll = float(window["low"].min())
    if ll <= 0 or pd.isna(hh) or pd.isna(ll):
        return None
    return round(max(0.0, (hh - ll) / ll * 100), 2)


def _volume_ratio(df: pd.DataFrame, lookback: int = _LOOKBACK) -> Optional[float]:
    """today's volume / average volume over last `lookback` sessions."""
    if "volume" not in df or df["volume"].notna().sum() < lookback:
        return None
    v = df["volume"].dropna()
    if len(v) < lookback:
        return None
    avg = float(v.tail(lookback).mean())
    if avg <= 0:
        return None
    return round(max(0.0, float(v.iloc[-1] / avg)), 2)


def _stage_label(df: pd.DataFrame) -> str:
    """Weinstein Stage 2 simplified — else not_stage2."""
    if len(df) < _SMA_150 + _SMA_SLOPE_LOOKBACK:
        return "not_stage2"
    close = df["close"]
    price = float(close.iloc[-1])
    sma150 = close.rolling(_SMA_150).mean()
    sma_now = sma150.iloc[-1]
    sma_prev = sma150.iloc[-1 - _SMA_SLOPE_LOOKBACK]
    if pd.isna(sma_now) or pd.isna(sma_prev):
        return "not_stage2"

    window = close.tail(252)
    low_52 = float(window.min())
    if low_52 <= 0:
        return "not_stage2"

    above_ma = price > float(sma_now)
    ma_rising = float(sma_now) > float(sma_prev)
    above_low = price >= low_52 * (1 + _STAGE2_ABOVE_LOW_PCT / 100)

    if above_ma and ma_rising and above_low:
        return "stage2_uptrend"
    return "not_stage2"


def _detect_patterns(df: pd.DataFrame) -> list[str]:
    patterns: list[str] = []
    if len(df) < 2 or "high" not in df or "low" not in df:
        return patterns

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # Inside Bar: today's range fully inside yesterday's
    if (
        len(df) >= 2
        and not pd.isna(high.iloc[-1])
        and not pd.isna(low.iloc[-1])
        and not pd.isna(high.iloc[-2])
        and not pd.isna(low.iloc[-2])
        and high.iloc[-1] <= high.iloc[-2]
        and low.iloc[-1] >= low.iloc[-2]
    ):
        patterns.append("inside_bar")

    # NR4: today's range is the narrowest of the last 4 days
    if len(df) >= 4:
        ranges = (high - low).tail(4)
        if ranges.notna().all():
            today_range = float(ranges.iloc[-1])
            if today_range <= float(ranges.min()) + 1e-12:
                patterns.append("nr4")

    # 52-week high / low breakout on close
    window = close.tail(252)
    if len(window) >= 2:
        prior_max = float(window.iloc[:-1].max())
        prior_min = float(window.iloc[:-1].min())
        last = float(close.iloc[-1])
        if last >= prior_max:
            patterns.append("52w_high_breakout")
        if last <= prior_min:
            patterns.append("52w_low_breakout")

    return patterns


def compute_chart_shape(bars, ticker: str = "") -> ChartShape:
    df = to_frame(bars)
    if df.empty or df["close"].dropna().empty:
        return ChartShape(stage="not_stage2", patterns=[])

    df = df.dropna(subset=["close"]).reset_index(drop=True)
    close = df["close"]
    price = float(close.iloc[-1])

    trend_50 = _slope_label(close, 50)
    trend_200 = _slope_label(close, 200)

    # --- existing Phase 1 prose / Darvas helpers (unchanged logic) ---
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

    # --- Phase B signals ---
    consol_pct = _consolidation_pct(df)
    is_consolidating = (
        consol_pct is not None and consol_pct <= _CONSOL_TIGHT_PCT
    )
    vol_ratio = _volume_ratio(df)
    volume_confirmed = (
        vol_ratio is not None and vol_ratio >= _VOL_CONFIRM
    )
    stage = _stage_label(df)
    if stage not in ("stage2_uptrend", "not_stage2"):
        stage = "not_stage2"
    patterns = _detect_patterns(df)

    if ticker:
        pat_str = ",".join(patterns) if patterns else "none"
        print(
            f"[TECHNICALS] {ticker} — consolidation {consol_pct if consol_pct is not None else '?'}%, "
            f"volume_ratio {vol_ratio if vol_ratio is not None else '?'}x, {stage}, "
            f"patterns: {pat_str}"
        )

    return ChartShape(
        trend_50d=trend_50,
        trend_200d=trend_200,
        consolidation=consolidation,
        volume_pattern=volume_pattern,
        distance_note=distance_note,
        box_width_pct=box_width_pct,
        breakout_above_box=breakout_above_box,
        consolidation_percentage=consol_pct,
        is_consolidating=is_consolidating if consol_pct is not None else None,
        volume_ratio=vol_ratio,
        volume_confirmed_breakout=volume_confirmed if vol_ratio is not None else None,
        stage=stage,
        patterns=patterns,
    )
