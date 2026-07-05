"""Dossier-powered screening — bridges selector pipeline to backend API shape."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from strategies import STRATEGY_NAMES, VALID_STRATEGIES

# Backend runs with cwd=backend/; selector + data_layer live at repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from selector.pipeline import run_pipeline  # noqa: E402
from selector.schemas import FinalPicks, Pick, StockVerdict  # noqa: E402

log = logging.getLogger(__name__)


def _fmt_inr(n: float) -> str:
    return "₹" + f"{round(n):,}"


def _load_dossier_meta() -> dict[str, dict]:
    from data_layer.storage import load_all_dossiers

    meta = {}
    for d in load_all_dossiers():
        meta[d.meta.ticker] = {
            "name": d.meta.name or d.meta.ticker,
            "sector": d.meta.sector or "—",
            "as_of": d.meta.as_of,
        }
    return meta


def _verdict_for(scoring: list[dict], ticker: str) -> dict | None:
    for v in scoring:
        if v.get("ticker") == ticker:
            return v
    return None


def _build_pick_report(
    pick: Pick,
    strategy: str,
    dossier_meta: dict,
    verdict: dict | None = None,
) -> dict:
    buy = pick.buy_price
    sell = pick.sell_target
    stop = pick.stop_loss
    upside_pct = round((sell - buy) / buy * 100) if buy > 0 else 0
    dm = dossier_meta.get(pick.ticker, {})

    signals = [{"label": "Conviction", "detail": str(pick.conviction)}]
    if verdict:
        for sig in verdict.get("key_signals") or []:
            signals.append({"label": "Signal", "detail": str(sig)})
        for risk in verdict.get("risks") or []:
            signals.append({"label": "Risk", "detail": str(risk)})

    sections = [{"title": "Why this stock", "body": pick.rationale}]
    if verdict and verdict.get("thesis") and verdict["thesis"] != pick.rationale:
        sections.append({"title": "Thesis", "body": verdict["thesis"]})

    return {
        "ticker": pick.ticker,
        "name": dm.get("name", pick.ticker),
        "sector": dm.get("sector", "—"),
        "strategy": strategy,
        "strategyName": STRATEGY_NAMES.get(strategy, strategy),
        "headline": f"AI pick · conviction {pick.conviction}",
        "summary": pick.rationale,
        "sections": sections,
        "signals": signals,
        "tradePlan": [
            {"label": "Buy at", "value": f"₹{buy:,.2f}", "hint": "From dossier EOD price"},
            {"label": "Sell target", "value": f"₹{sell:,.0f}", "hint": f"~{upside_pct}% upside"},
            {"label": "Position size", "value": f"{pick.shares} shares · {_fmt_inr(pick.allocation_inr)}", "hint": "From final allocation"},
            {"label": "Stop-loss", "value": f"₹{stop:,.2f}", "hint": "LLM stop level"},
        ],
        "llmRank": None,
        "isAiPick": True,
        "upside": f"+{max(upside_pct, 0)}%",
        "dossierAsOf": dm.get("as_of"),
        "reasoningOneLiner": pick.rationale,
    }


def _pick_to_candidate(
    pick: Pick,
    rank: int,
    budget: int,
    strategy: str,
    dossier_meta: dict,
    verdict: dict | None = None,
) -> dict:
    buy = pick.buy_price
    sell = pick.sell_target
    shares = int(pick.shares)
    cost = round(pick.allocation_inr or shares * buy, 2)
    upside_pct = round((sell - buy) / buy * 100) if buy > 0 else 0
    dm = dossier_meta.get(pick.ticker, {})

    candidate = {
        "ticker": pick.ticker,
        "name": dm.get("name", pick.ticker),
        "sector": dm.get("sector", "—"),
        "buyPrice": buy,
        "sellPrice": round(sell, 2),
        "stopLoss": round(pick.stop_loss, 2),
        "buyFmt": f"₹{buy:,.2f}",
        "sellFmt": f"₹{round(sell):,.0f}",
        "passCount": pick.conviction,
        "passAll": True,
        "recLabel": f"Conviction {pick.conviction}",
        "recGood": True,
        "recNote": pick.rationale,
        "reasoningOneLiner": pick.rationale,
        "canLog": shares >= 1,
        "shares": shares,
        "cost": cost,
        "costFmt": _fmt_inr(cost),
        "leftFmt": _fmt_inr(max(0, budget - cost)),
        "upside": f"+{max(upside_pct, 0)}%",
        "gainNote": f"~{upside_pct}% to sell target",
        "signal": {},
        "llmRank": rank,
        "pickReport": _build_pick_report(pick, strategy, dossier_meta, verdict),
    }
    return candidate


def screen(strategy: str, budget: int, bot_context: dict | None = None, use_llm: bool = True) -> dict:
    """Run dossier funnel + selector LLM pipeline; return backend screen dict."""
    if strategy not in VALID_STRATEGIES:
        return {"strategy": strategy, "supported": False, "message": "Unknown strategy."}

    bot = bot_context or {}
    cash = bot.get("availableCash") or bot.get("available_cash") or budget
    cap_pct = bot.get("max_per_stock_pct") or bot.get("maxPerStockPct") or 40

    log.info(
        "screen start strategy=%s budget=₹%s cash=₹%s cap=%s%% llm=%s",
        strategy, budget, cash, cap_pct, use_llm,
    )

    try:
        payload = run_pipeline(
            strategy,
            budget=budget,
            cash_available=float(cash),
            per_stock_cap_pct=float(cap_pct),
            use_llm=use_llm,
            write_intentions=False,
        )
    except FileNotFoundError as e:
        log.error("screen failed: %s", e)
        return {
            "strategy": strategy,
            "strategyName": STRATEGY_NAMES.get(strategy, strategy),
            "supported": False,
            "pipeline": "dossier",
            "message": str(e),
        }

    result = FinalPicks.model_validate(payload["result"])
    scoring_summary = payload.get("scoringSummary") or []
    reasoning_log = payload.get("reasoningLog") or []
    dossier_meta = _load_dossier_meta()
    candidates = [
        _pick_to_candidate(
            p,
            rank,
            budget,
            strategy,
            dossier_meta,
            _verdict_for(scoring_summary, p.ticker),
        )
        for rank, p in enumerate(result.picks, start=1)
    ]

    mc = payload.get("market_context") or {}
    market_note = None
    if mc.get("nifty_above_200dma") is False:
        market_note = "Nifty below 200 DMA — defensive backdrop noted in dossiers."

    log.info(
        "screen done: %d dossiers → %d funnel survivors → %d picks (%.1fs)",
        payload.get("dossierCount", 0),
        payload.get("funnelSurvivors", 0),
        len(candidates),
        payload.get("elapsedSec", 0),
    )

    return {
        "strategy": strategy,
        "strategyName": STRATEGY_NAMES[strategy],
        "supported": True,
        "pipeline": "dossier",
        "budget": budget,
        "screenedCount": payload.get("dossierCount", 0),
        "funnelSurvivors": payload.get("funnelSurvivors", 0),
        "passedCount": len(candidates),
        "affordableCount": sum(1 for c in candidates if c["canLog"]),
        "candidates": candidates,
        "llm": {
            "used": use_llm,
            "picks": len(result.picks),
            "skipped": len(result.skipped),
            "elapsedSec": payload.get("elapsedSec"),
        },
        "llmSummary": result.portfolio_note,
        "reasoningLog": reasoning_log,
        "marketFilter": market_note,
        "dossierAsOf": dossier_meta.get(candidates[0]["ticker"], {}).get("as_of") if candidates else None,
    }
