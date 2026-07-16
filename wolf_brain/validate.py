"""Post-LLM validation for Wolf Brain outputs (first-line defense before executor)."""
from __future__ import annotations

import logging
import math

from wolf_brain.schemas import (
    BrainPick,
    DailyReviewBrainOutput,
    DeployBrainOutput,
    HoldingReview,
)

log = logging.getLogger(__name__)

DEFAULT_MIN_TRADE_VALUE = 10000.0


def normalize_guardrails(raw: dict | None) -> dict[str, float]:
    g = raw or {}
    return {
        "stop_loss_pct": float(g.get("stop_loss_pct") or 15.0),
        "max_daily_loss_pct": float(g.get("max_daily_loss_pct") or 5.0),
        "max_capital_deployed_pct": float(
            g.get("max_capital_deployed_pct") or g.get("max_deployed_pct") or 100.0
        ),
        "max_per_stock_pct": float(
            g.get("max_per_stock_pct") or g.get("max_position_pct") or 40.0
        ),
        "min_trade_value": float(g.get("min_trade_value") or DEFAULT_MIN_TRADE_VALUE),
    }


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _line_cost(pick: BrainPick) -> float:
    return round(int(pick.quantity) * float(pick.buy_price), 2)


def _validate_price_geometry(
    pick: BrainPick, guardrails: dict[str, float]
) -> BrainPick | None:
    """Drop picks with inverted or implausible buy / target / stop geometry."""
    buy = float(pick.buy_price)
    target = float(pick.target)
    stop = float(pick.stop_loss)
    sym = pick.symbol

    if target <= buy:
        log.warning(
            "[WOLF BRAIN] dropped %s: target ₹%.2f not above buy ₹%.2f",
            sym,
            target,
            buy,
        )
        return None
    if stop <= 0 or stop >= buy:
        log.warning(
            "[WOLF BRAIN] dropped %s: stop_loss ₹%.2f invalid for buy ₹%.2f",
            sym,
            stop,
            buy,
        )
        return None

    min_upside_pct = 1.0
    upside_pct = (target - buy) / buy * 100
    if upside_pct < min_upside_pct:
        log.warning(
            "[WOLF BRAIN] dropped %s: target upside %.1f%% below minimum %.1f%%",
            sym,
            upside_pct,
            min_upside_pct,
        )
        return None

    stop_pct = guardrails["stop_loss_pct"]
    actual_stop_pct = (buy - stop) / buy * 100
    if actual_stop_pct < stop_pct * 0.5 or actual_stop_pct > stop_pct * 2.0:
        log.warning(
            "[WOLF BRAIN] dropped %s: stop %.1f%% below buy vs guardrail %.1f%%",
            sym,
            actual_stop_pct,
            stop_pct,
        )
        return None

    return pick


def _sanitize_pick(pick: BrainPick, guardrails: dict[str, float]) -> BrainPick | None:
    qty = int(math.floor(pick.quantity))
    if qty < 1:
        return None
    buy = float(pick.buy_price)
    if buy <= 0:
        log.warning("[WOLF BRAIN] dropped %s: invalid buy_price", pick.symbol)
        return None

    pick = pick.model_copy(
        update={"quantity": qty, "symbol": _normalize_symbol(pick.symbol)}
    )
    pick = _validate_price_geometry(pick, guardrails)
    if pick is None:
        return None

    cost = round(qty * float(pick.buy_price), 2)
    if cost < guardrails["min_trade_value"]:
        log.warning(
            "[WOLF BRAIN] dropped %s: cost ₹%.0f below min_trade_value ₹%.0f",
            pick.symbol,
            cost,
            guardrails["min_trade_value"],
        )
        return None

    return pick


def _trim_picks_to_cash(
    picks: list[BrainPick],
    cash_available: float,
    guardrails: dict[str, float],
) -> list[BrainPick]:
    sanitized: list[BrainPick] = []
    for p in picks:
        s = _sanitize_pick(p, guardrails)
        if s is not None:
            sanitized.append(s)

    kept: list[BrainPick] = []
    remaining = float(cash_available)
    for p in sorted(sanitized, key=lambda x: -x.conviction):
        cost = _line_cost(p)
        if cost > remaining + 0.01:
            log.warning(
                "[WOLF BRAIN] dropped %s: cost ₹%.2f exceeds remaining cash ₹%.2f",
                p.symbol,
                cost,
                remaining,
            )
            continue
        kept.append(p)
        remaining = round(remaining - cost, 2)
    return kept


def validate_deploy_output(
    result: DeployBrainOutput,
    *,
    cash_available: float,
    guardrails: dict[str, float],
    shortlist_symbols: set[str],
) -> DeployBrainOutput:
    picks: list[BrainPick] = []
    for p in result.picks:
        sym = _normalize_symbol(p.symbol)
        if sym not in shortlist_symbols:
            log.warning("[WOLF BRAIN] dropped %s: not in shortlist", sym)
            continue
        picks.append(p.model_copy(update={"symbol": sym}))

    trimmed = _trim_picks_to_cash(picks, cash_available, guardrails)
    return result.model_copy(update={"picks": trimmed})


def validate_daily_review_output(
    result: DailyReviewBrainOutput,
    *,
    cash_available: float,
    guardrails: dict[str, float],
    shortlist_symbols: set[str],
    held_symbols: set[str],
) -> DailyReviewBrainOutput:
    reviews_by_sym: dict[str, HoldingReview] = {}
    for r in result.holdings_review:
        sym = _normalize_symbol(r.symbol)
        if sym in held_symbols:
            reviews_by_sym[sym] = r.model_copy(update={"symbol": sym})

    for sym in held_symbols:
        if sym not in reviews_by_sym:
            reviews_by_sym[sym] = HoldingReview(
                symbol=sym,
                verdict="hold",
                reasoning="No review returned by model; defaulting to hold.",
            )

    new_picks: list[BrainPick] = []
    for p in result.new_picks:
        sym = _normalize_symbol(p.symbol)
        if sym in held_symbols:
            log.warning("[WOLF BRAIN] dropped new pick %s: already held", sym)
            continue
        if sym not in shortlist_symbols:
            log.warning("[WOLF BRAIN] dropped new pick %s: not in shortlist", sym)
            continue
        new_picks.append(p.model_copy(update={"symbol": sym}))

    trimmed = _trim_picks_to_cash(new_picks, cash_available, guardrails)
    return result.model_copy(
        update={
            "holdings_review": list(reviews_by_sym.values()),
            "new_picks": trimmed,
        }
    )
