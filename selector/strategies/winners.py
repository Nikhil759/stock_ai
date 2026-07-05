"""
Winners — "Buy the winners" (positional momentum + strength).

Coarse funnel over the dossier's already-computed technicals/market_context.
Survive if 3-of-4 signals fire (stock momentum + optional market health).
Market weakness is not a hard block — the LLM applies a higher bar instead.
"""
from __future__ import annotations

MIN_PASSES = 3


def passes(dossier) -> tuple[bool, dict]:
    t = dossier.technicals
    mc = dossier.market_context

    checks = {
        "above_50_and_200dma": bool(t.above_50dma) and bool(t.above_200dma),
        "near_52w_high": t.pct_from_52w_high is not None and t.pct_from_52w_high >= -15,
        "beating_nifty_6m": t.rel_strength_vs_nifty_6m is not None and t.rel_strength_vs_nifty_6m > 0,
        "market_healthy": mc.nifty_above_200dma is True,
    }
    survived = sum(checks.values()) >= MIN_PASSES
    return survived, checks


def rank_key(dossier) -> tuple:
    """Higher tuple sorts first — secondary quality when funnel scores tie."""
    t = dossier.technicals
    f = dossier.fundamentals
    cs = dossier.chart_shape

    rising_vol = (cs.volume_pattern or "").startswith("rising")
    tight_base = (cs.consolidation or "").startswith("tight")
    roe_ok = f.roe is not None and f.roe >= 12
    pe_ok = f.pe is not None and 5 < f.pe < 60
    vol_ratio = t.volume_vs_20d_avg or 0.0
    rs6 = t.rel_strength_vs_nifty_6m if t.rel_strength_vs_nifty_6m is not None else -999.0
    pct_hi = t.pct_from_52w_high if t.pct_from_52w_high is not None else -999.0

    secondary = int(rising_vol) + int(tight_base) + int(roe_ok) + int(pe_ok)
    return (secondary, vol_ratio, rs6, pct_hi)
