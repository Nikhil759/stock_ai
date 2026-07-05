"""
Box — "Buy the box breakout" (Darvas Box, technical only).

Coarse funnel: 2-of-3 among tight consolidation, rising volume, and a
computed breakout above the prior 20-day box. Nifty above 200 DMA is tracked
for ranking/logging but is NOT a hard gate — weak tape lowers LLM conviction
instead of zeroing the pipeline.
"""
from __future__ import annotations

MIN_PASSES = 2
CORE_CHECKS = ("tight_consolidation", "volume_surge", "breakout_above_box")


def passes(dossier) -> tuple[bool, dict]:
    cs = dossier.chart_shape
    mc = dossier.market_context

    consolidation = (cs.consolidation or "").lower()
    volume_pattern = (cs.volume_pattern or "").lower()

    checks = {
        "tight_consolidation": consolidation.startswith("tight"),
        "volume_surge": volume_pattern.startswith("rising"),
        "breakout_above_box": cs.breakout_above_box is True,
        "market_healthy": mc.nifty_above_200dma is True,
    }
    core_passes = sum(checks[k] for k in CORE_CHECKS)
    survived = core_passes >= MIN_PASSES
    return survived, checks


def rank_key(dossier) -> tuple:
    """Higher tuple sorts first — used when funnel scores tie."""
    cs = dossier.chart_shape
    mc = dossier.market_context
    width = cs.box_width_pct if cs.box_width_pct is not None else 99.0
    return (
        int(cs.breakout_above_box is True),
        int((cs.volume_pattern or "").startswith("rising")),
        int((cs.consolidation or "").startswith("tight")),
        -width,
        int(mc.nifty_above_200dma is True),
    )
