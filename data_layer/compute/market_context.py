"""
Phase 1 — the shared market backdrop. Computed ONCE per run and copied into
every dossier. Needs the Nifty + India VIX series (fetched once) and the set
of per-stock technicals (for breadth).
"""
from __future__ import annotations
from typing import Optional
import pandas as pd

from ..dossier import MarketContext
from ..config import VIX_CALM, VIX_ELEVATED
from .technicals import _sma
from .chart_shape import _slope_label


def _vix_regime(vix: Optional[float]) -> Optional[str]:
    if vix is None:
        return None
    if vix < VIX_CALM:
        return "calm"
    if vix < VIX_ELEVATED:
        return "elevated"
    return "high"


def compute_market_context(nifty_closes, vix_value, above_200_flags):
    """
    nifty_closes: list/Series of Nifty closes
    vix_value: latest India VIX value (float) or None
    above_200_flags: list of bool/None -> each stock's above_200dma, for breadth
    Returns a MarketContext WITHOUT per-stock sector fields (those are set per
    dossier by the builder).
    """
    ns = pd.Series(nifty_closes).dropna().reset_index(drop=True) if nifty_closes is not None else pd.Series(dtype=float)

    nifty_above = None
    nifty_trend = None
    if not ns.empty:
        price = float(ns.iloc[-1])
        dma200 = _sma(ns, 200)
        if dma200:
            nifty_above = price > dma200
        nifty_trend = _slope_label(ns, 50)

    # breadth: % of stocks trading above their 200 DMA
    breadth = None
    flags = [f for f in above_200_flags if f is not None]
    if flags:
        breadth = round(sum(1 for f in flags if f) / len(flags) * 100, 1)

    return MarketContext(
        nifty_above_200dma=nifty_above,
        nifty_trend=nifty_trend,
        india_vix=round(float(vix_value), 2) if vix_value is not None else None,
        vix_regime=_vix_regime(vix_value),
        market_breadth_pct_above_200dma=breadth,
    )
