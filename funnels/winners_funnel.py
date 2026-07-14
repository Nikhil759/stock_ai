"""
Winners math funnel — momentum / Stage-2 screen.

Tunable thresholds at top. Pure deterministic filtering; no LLM.
"""
from __future__ import annotations

from typing import Any

from .common import apply_step, finish, wrap_dossiers

# --- tunable thresholds ---
PCT_FROM_52W_HIGH_MIN = -15.0  # within 15% of 52w high
FII_CHANGE_MIN = 0.0  # flat or increasing QoQ (pp); None treated as unavailable→pass

STRATEGY = "Winners"


def _f(d: dict) -> dict:
    return d.get("fundamentals") or {}


def _t(d: dict) -> dict:
    return d.get("technicals") or {}


def _cs(d: dict) -> dict:
    return d.get("chart_shape") or {}


def run_winners_funnel(dossiers: list[Any]) -> list[dict]:
    rows = wrap_dossiers(dossiers)
    print(f"[MATH FUNNEL] {STRATEGY}: start n={len(rows)}")

    def earnings_growth_ok(d: dict):
        g = _f(d).get("earnings_growth_yoy")
        # Field often empty until fundamentals_ext fills it — require >0 when present;
        # when missing, fall back to positive 3m absolute return as a temporary proxy.
        if g is not None:
            g = float(g)
            if g <= 0:
                return False, f"earnings_growth_yoy {g:.1f}% is not > 0", None
            return True, None, {"earnings_growth_yoy": g}
        r3 = _t(d).get("return_3m")
        if r3 is None:
            return False, "earnings_growth_yoy missing and return_3m missing", None
        r3 = float(r3)
        if r3 <= 0:
            return False, (
                f"earnings_growth_yoy missing; return_3m {r3:.1f}% proxy not > 0"
            ), None
        return True, None, {
            "earnings_growth_yoy": None,
            "earnings_proxy": "return_3m>0 (YoY earnings growth not on dossier)",
            "return_3m": r3,
        }

    def near_high_ok(d: dict):
        pct = _t(d).get("pct_from_52w_high")
        if pct is None:
            return False, "pct_from_52w_high missing", None
        pct = float(pct)
        if pct < PCT_FROM_52W_HIGH_MIN:
            return False, (
                f"pct_from_52w_high {pct:.1f}% below floor of {PCT_FROM_52W_HIGH_MIN:g}%"
            ), None
        return True, None, {
            "pct_from_52w_high": pct,
            "pct_from_52w_high_min": PCT_FROM_52W_HIGH_MIN,
        }

    def rs_ok(d: dict):
        rs = _t(d).get("rel_strength_vs_nifty_3m")
        if rs is None:
            return False, "rel_strength_vs_nifty_3m missing", None
        rs = float(rs)
        if rs <= 0:
            return False, f"rel_strength_vs_nifty_3m {rs:.2f} is not positive", None
        return True, None, {"rel_strength_vs_nifty_3m": rs}

    def fii_ok(d: dict):
        chg = _f(d).get("fii_holding_change_qoq")
        if chg is None:
            # Not on dossier for most names yet — do not hard-fail the whole funnel.
            return True, None, {
                "fii_holding_change_qoq": None,
                "fii_trend": "unavailable_assumed_non_declining",
            }
        chg = float(chg)
        if chg < FII_CHANGE_MIN:
            return False, f"fii_holding_change_qoq {chg:.2f} is declining", None
        return True, None, {"fii_holding_change_qoq": chg, "fii_trend": "flat_or_up"}

    def stage_ok(d: dict):
        stage = _cs(d).get("stage")
        if stage != "stage2_uptrend":
            return False, f"stage={stage!r} (need stage2_uptrend)", None
        return True, None, {"stage": stage}

    rows = apply_step(STRATEGY, "earnings growth > 0% YoY (or return_3m proxy)", rows, earnings_growth_ok)
    rows = apply_step(
        STRATEGY,
        f"within {-PCT_FROM_52W_HIGH_MIN:g}% of 52w high",
        rows,
        near_high_ok,
    )
    rows = apply_step(STRATEGY, "rel strength vs Nifty 3m > 0", rows, rs_ok)
    rows = apply_step(STRATEGY, "FII trend flat/up (or unavailable)", rows, fii_ok)
    rows = apply_step(STRATEGY, "stage2_uptrend", rows, stage_ok)
    return finish(STRATEGY, rows)
