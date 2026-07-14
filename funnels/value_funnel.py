"""
Value math funnel — Graham-style cheap quality screen.

Tunable thresholds at top. Pure deterministic filtering; no LLM.
"""
from __future__ import annotations

import math
from typing import Any

from .common import apply_step, finish, wrap_dossiers

# --- tunable thresholds ---
PE_MAX = 30.0
DEBT_TO_EQUITY_MAX = 1.0
MARKET_CAP_MIN_CR = 5_000.0  # ₹ crore liquidity floor
GRAHAM_MULTIPLIER = 22.5  # classic Graham number: sqrt(22.5 * EPS * BVPS)
# Allow price up to this multiple of Graham fair value (1.0 = strict classic).
GRAHAM_PRICE_MAX_RATIO = 1.15

STRATEGY = "Value"


def _f(d: dict) -> dict:
    return d.get("fundamentals") or {}


def _graham_fair_value(f: dict) -> float | None:
    """Prefer dossier graham_number; else sqrt(22.5 * EPS * BVPS)."""
    gn = f.get("graham_number")
    if gn is not None:
        try:
            return float(gn)
        except (TypeError, ValueError):
            pass
    eps = f.get("eps_ttm")
    price = f.get("price")
    pb = f.get("pb")
    try:
        eps_f = float(eps) if eps is not None else None
        pb_f = float(pb) if pb is not None else None
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        return None
    if eps_f is None or eps_f <= 0 or pb_f is None or pb_f <= 0 or price_f is None:
        return None
    bvps = price_f / pb_f
    return math.sqrt(GRAHAM_MULTIPLIER * eps_f * bvps)


def run_value_funnel(dossiers: list[Any]) -> list[dict]:
    rows = wrap_dossiers(dossiers)
    print(f"[MATH FUNNEL] {STRATEGY}: start n={len(rows)}")

    def pe_ok(d: dict):
        pe = _f(d).get("pe")
        if pe is None:
            return False, "P/E missing", None
        pe = float(pe)
        if pe <= 0:
            return False, f"P/E {pe} not positive", None
        if pe > PE_MAX:
            return False, f"P/E {pe:.1f} exceeds ceiling of {PE_MAX:g}", None
        return True, None, {"pe": pe, "pe_max": PE_MAX}

    def de_ok(d: dict):
        de = _f(d).get("debt_to_equity")
        if de is None:
            return False, "debt/equity missing", None
        de = float(de)
        if de > DEBT_TO_EQUITY_MAX:
            return False, f"debt/equity {de:.2f} exceeds ceiling of {DEBT_TO_EQUITY_MAX:g}", None
        return True, None, {"debt_to_equity": de, "debt_to_equity_max": DEBT_TO_EQUITY_MAX}

    def earnings_ok(d: dict):
        # Dossiers do not yet store 3-year EPS history. Proxy: positive TTM EPS
        # (and non-negative earnings_growth_yoy when present).
        f = _f(d)
        eps = f.get("eps_ttm")
        if eps is None:
            return False, "eps_ttm missing (3y earnings history not on dossier)", None
        eps = float(eps)
        if eps <= 0:
            return False, f"eps_ttm {eps} is not positive", None
        g = f.get("earnings_growth_yoy")
        if g is not None and float(g) < 0:
            return False, f"earnings_growth_yoy {float(g):.1f}% is negative", None
        facts = {"eps_ttm": eps, "earnings_proxy": "eps_ttm>0 (3y series not on dossier yet)"}
        if g is not None:
            facts["earnings_growth_yoy"] = float(g)
        return True, None, facts

    def graham_ok(d: dict):
        f = _f(d)
        price = f.get("price")
        if price is None:
            return False, "price missing", None
        price = float(price)
        fair = _graham_fair_value(f)
        if fair is None:
            return False, "Graham fair value not computable (need EPS + book)", None
        ceiling = fair * GRAHAM_PRICE_MAX_RATIO
        if price >= ceiling:
            return False, (
                f"price {price:.2f} not below Graham ceiling "
                f"{ceiling:.2f} (fair {fair:.2f} × {GRAHAM_PRICE_MAX_RATIO:g})"
            ), None
        return True, None, {
            "price": price,
            "graham_fair_value": round(fair, 2),
            "graham_price_ceiling": round(ceiling, 2),
            "graham_price_max_ratio": GRAHAM_PRICE_MAX_RATIO,
        }

    def mcap_ok(d: dict):
        mcap = _f(d).get("market_cap_cr")
        if mcap is None:
            return False, "market_cap_cr missing", None
        mcap = float(mcap)
        if mcap < MARKET_CAP_MIN_CR:
            return False, f"market_cap ₹{mcap:.0f}cr below floor of ₹{MARKET_CAP_MIN_CR:g}cr", None
        return True, None, {"market_cap_cr": mcap, "market_cap_min_cr": MARKET_CAP_MIN_CR}

    rows = apply_step(STRATEGY, f"P/E ≤ {PE_MAX:g}", rows, pe_ok)
    rows = apply_step(STRATEGY, f"debt/equity ≤ {DEBT_TO_EQUITY_MAX:g}", rows, de_ok)
    rows = apply_step(STRATEGY, "positive earnings (TTM proxy)", rows, earnings_ok)
    rows = apply_step(
        STRATEGY,
        f"price below Graham × {GRAHAM_PRICE_MAX_RATIO:g}",
        rows,
        graham_ok,
    )
    rows = apply_step(STRATEGY, f"market_cap ≥ ₹{MARKET_CAP_MIN_CR:g}cr", rows, mcap_ok)
    return finish(STRATEGY, rows)
