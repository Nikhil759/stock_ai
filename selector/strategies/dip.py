"""
Dip — "Buy the dip" (Connors RSI-2 mean reversion, technical only).

The old backend/screeners/dip.py required RSI(2) < 10; loosened to < 15 here
so borderline washouts survive for the LLM to judge.
"""
from __future__ import annotations


def passes(dossier) -> tuple[bool, dict]:
    t = dossier.technicals

    checks = {
        "above_200dma": t.above_200dma is True,
        "rsi2_lt_15": t.rsi_2 is not None and t.rsi_2 < 15,
    }
    survived = all(checks.values())
    return survived, checks
