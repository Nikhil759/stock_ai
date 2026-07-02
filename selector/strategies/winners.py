"""
Winners — "Buy the winners" (positional momentum + strength).

Coarse funnel over the dossier's already-computed technicals/market_context
(the old backend/screeners/winners.py scored raw closes/volumes live; here
the data layer has already done that work). Survive if most of the four
signals fire -- loosened to 3-of-4 rather than requiring all.
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
