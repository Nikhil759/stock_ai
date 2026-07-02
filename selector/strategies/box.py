"""
Box — "Buy the box breakout" (Darvas Box, technical only).

The old backend/screeners/box.py computed the box numerically from raw
highs/lows/volumes (2-12% width, breakout above box*1.002, volume >=1.25x
avg). The data layer only exposes chart_shape as plain-language labels
(consolidation/volume_pattern strings), not the raw box geometry, so this
reads those labels instead. This is deliberately strict -- few or zero
survivors is a normal, healthy outcome (per the spec), so all three must
fire rather than a majority.
"""
from __future__ import annotations


def passes(dossier) -> tuple[bool, dict]:
    cs = dossier.chart_shape
    mc = dossier.market_context

    consolidation = (cs.consolidation or "").lower()
    volume_pattern = (cs.volume_pattern or "").lower()

    checks = {
        "tight_consolidation": consolidation.startswith("tight"),
        "volume_surge": volume_pattern.startswith("rising"),
        "market_healthy": mc.nifty_above_200dma is True,
    }
    survived = all(checks.values())
    return survived, checks
