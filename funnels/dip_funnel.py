"""
Dip math funnel — Connors RSI-2 mean-reversion screen.

Tunable thresholds at top. Pure deterministic filtering; no LLM.
Expect fewer survivors than other strategies on a typical day.
"""
from __future__ import annotations

from typing import Any

from .common import apply_step, finish, wrap_dossiers

# --- tunable thresholds ---
# Connors classic is <10; loosened so daily screens keep a usable shortlist.
RSI2_MAX = 20.0

STRATEGY = "Dip"


def _t(d: dict) -> dict:
    return d.get("technicals") or {}


def run_dip_funnel(dossiers: list[Any]) -> list[dict]:
    rows = wrap_dossiers(dossiers)
    print(f"[MATH FUNNEL] {STRATEGY}: start n={len(rows)}")
    print(
        f"[MATH FUNNEL] {STRATEGY}: note — this funnel often yields a short list; "
        "that is expected for RSI(2) extremes"
    )

    def rsi_ok(d: dict):
        rsi2 = _t(d).get("rsi_2")
        if rsi2 is None:
            return False, "rsi_2 missing", None
        rsi2 = float(rsi2)
        if rsi2 >= RSI2_MAX:
            return False, f"rsi_2 {rsi2:.1f} not below threshold of {RSI2_MAX:g}", None
        return True, None, {"rsi_2": rsi2, "rsi2_max": RSI2_MAX}

    def above_200_ok(d: dict):
        t = _t(d)
        flag = t.get("above_200dma")
        dma200 = t.get("dma_200")
        if flag is not True:
            return False, f"above_200dma={flag!r} (dma_200={dma200})", None
        return True, None, {"above_200dma": True, "dma_200": dma200}

    rows = apply_step(STRATEGY, f"RSI(2) < {RSI2_MAX:g}", rows, rsi_ok)
    rows = apply_step(STRATEGY, "price above 200-day MA", rows, above_200_ok)
    return finish(STRATEGY, rows)
