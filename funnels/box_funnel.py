"""
Box math funnel — Darvas / consolidation breakout screen.

Tunable thresholds at top. Pure deterministic filtering; no LLM.
"""
from __future__ import annotations

from typing import Any

from .common import apply_step, finish, wrap_dossiers

# --- tunable thresholds ---
CONSOLIDATION_PCT_MAX = 15.0  # Phase B default was 10; loosened for daily depth
VOLUME_RATIO_MIN = 1.0  # at least average volume (PKScreener classic is 2.5x)
# Breakout OR coiled near 52w high while in a tight range
PCT_FROM_52W_HIGH_MIN = -15.0

STRATEGY = "Box"


def _t(d: dict) -> dict:
    return d.get("technicals") or {}


def _cs(d: dict) -> dict:
    return d.get("chart_shape") or {}


def run_box_funnel(dossiers: list[Any]) -> list[dict]:
    rows = wrap_dossiers(dossiers)
    print(f"[MATH FUNNEL] {STRATEGY}: start n={len(rows)}")

    def stage_ok(d: dict):
        stage = _cs(d).get("stage")
        if stage != "stage2_uptrend":
            return False, f"stage={stage!r} (need stage2_uptrend)", None
        return True, None, {"stage": stage}

    def consolidating_ok(d: dict):
        cs = _cs(d)
        pct = cs.get("consolidation_percentage")
        width = cs.get("box_width_pct")
        tight = False
        if pct is not None and float(pct) <= CONSOLIDATION_PCT_MAX:
            tight = True
        if width is not None and float(width) <= CONSOLIDATION_PCT_MAX:
            tight = True
        if not tight:
            return False, (
                f"not tight (consolidation_percentage={pct}, "
                f"box_width_pct={width}, max={CONSOLIDATION_PCT_MAX:g})"
            ), None
        return True, None, {
            "consolidation_percentage": pct,
            "box_width_pct": width,
            "consolidation_pct_max": CONSOLIDATION_PCT_MAX,
            "is_consolidating": cs.get("is_consolidating"),
        }

    def breakout_ok(d: dict):
        cs = _cs(d)
        t = _t(d)
        if cs.get("breakout_above_box") is True:
            return True, None, {
                "breakout_above_box": True,
                "pct_from_52w_high": t.get("pct_from_52w_high"),
            }
        pct_hi = t.get("pct_from_52w_high")
        if pct_hi is not None and float(pct_hi) >= PCT_FROM_52W_HIGH_MIN:
            return True, None, {
                "breakout_above_box": False,
                "pct_from_52w_high": float(pct_hi),
                "pct_from_52w_high_min": PCT_FROM_52W_HIGH_MIN,
                "breakout_note": "coiled near 52w high inside tight range",
            }
        return False, (
            f"no breakout (breakout_above_box={cs.get('breakout_above_box')}, "
            f"pct_from_52w_high={pct_hi}, need ≥ {PCT_FROM_52W_HIGH_MIN:g}%)"
        ), None

    def volume_ok(d: dict):
        cs = _cs(d)
        ratio = cs.get("volume_ratio")
        if ratio is None:
            return False, "volume_ratio missing", None
        ratio = float(ratio)
        if ratio < VOLUME_RATIO_MIN:
            return False, (
                f"volume_ratio {ratio:.2f}x below minimum of {VOLUME_RATIO_MIN:g}x"
            ), None
        return True, None, {
            "volume_ratio": ratio,
            "volume_ratio_min": VOLUME_RATIO_MIN,
            "volume_confirmed_breakout": cs.get("volume_confirmed_breakout"),
        }

    rows = apply_step(STRATEGY, "stage2_uptrend", rows, stage_ok)
    rows = apply_step(
        STRATEGY,
        f"tight range (consol/box ≤ {CONSOLIDATION_PCT_MAX:g}%)",
        rows,
        consolidating_ok,
    )
    rows = apply_step(
        STRATEGY,
        "breakout or coiled near 52w high",
        rows,
        breakout_ok,
    )
    rows = apply_step(
        STRATEGY,
        f"volume_ratio ≥ {VOLUME_RATIO_MIN:g}x",
        rows,
        volume_ok,
    )

    nr4 = [
        r["symbol"]
        for r in rows
        if "nr4" in ((_cs(r["dossier"]).get("patterns")) or [])
    ]
    if rows:
        print(
            f"[MATH FUNNEL] {STRATEGY}: NR4 among survivors "
            f"{len(nr4)}/{len(rows)}"
            + (f" e.g. {', '.join(nr4[:5])}" if nr4 else "")
        )
        for r in rows:
            pats = (_cs(r["dossier"]).get("patterns")) or []
            r["funnel_reasons"]["patterns"] = pats
            r["funnel_reasons"]["has_nr4"] = "nr4" in pats

    return finish(STRATEGY, rows)
