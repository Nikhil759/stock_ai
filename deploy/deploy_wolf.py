"""New wolf deployment — Wolf Brain + Wolf Executor on Supabase."""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from cache.shortlist_cache import load_shortlist_resolved
from deploy.enrich_shortlist import enrich_shortlist_with_dossiers
from deploy.live_prices import apply_live_shortlist_prices
from backend.dossier_sync import sync_dossiers_from_api
from data_layer.storage import load_all_dossiers
from db import repository as repo
from db.repository import RUN_TYPE_BIRTH
from wolf_brain import run_wolf_brain
from wolf_executor import run_wolf_executor
from wolf_executor.kite_stub import live_orders_allowed
from wolf_executor.mode_util import executor_mode_from_wolf

log = logging.getLogger(__name__)

_STRATEGY_CODES = {
    "value": "VALUE",
    "winners": "WINNERS",
    "box": "BOX",
    "dip": "DIP",
}


def supabase_deploy_enabled() -> bool:
    return os.getenv("WOLF_SUPABASE_DEPLOY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def resolve_deploy_user_id(header_value: str | None = None) -> UUID | None:
    raw = (header_value or os.getenv("WOLF_DEPLOY_USER_ID") or "").strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        log.warning("invalid deploy user_id: %r", raw)
        return None


def _market_context() -> dict[str, Any]:
    dossiers = load_all_dossiers()
    if not dossiers:
        return {}
    mc = dossiers[0].market_context
    d = mc.__dict__ if hasattr(mc, "__dict__") else mc
    return {
        "nifty_trend": d.get("nifty_trend"),
        "vix": d.get("india_vix"),
        "nifty_above_200dma": d.get("nifty_above_200dma"),
        "market_breadth_pct_above_200dma": d.get(
            "market_breadth_pct_above_200dma"
        ),
    }


def guardrails_from_deploy_request(
    *,
    stop_loss_pct: float,
    max_daily_loss_pct: float,
    max_deployed_pct: float,
    max_per_stock_pct: float,
    min_trade_value: float = 10000.0,
) -> dict[str, float]:
    return {
        "stop_loss_pct": stop_loss_pct,
        "max_daily_loss_pct": max_daily_loss_pct,
        "max_capital_deployed_pct": max_deployed_pct,
        "max_per_stock_pct": max_per_stock_pct,
        "min_trade_value": min_trade_value,
    }


def _brain_buys(picks: list[dict]) -> list[dict]:
    return [
        {
            "symbol": p["symbol"],
            "quantity": int(p.get("quantity") or 0),
            "buy_price": float(p.get("buy_price") or 0),
            "target": float(p.get("target") or 0),
            "stop_loss": float(p.get("stop_loss") or 0),
        }
        for p in picks
    ]


def _log_birth_intents(
    wolf_id: str,
    run_date: date,
    picks: list[dict],
) -> list[dict]:
    rows: list[dict] = []
    for pick in picks:
        qty = int(pick.get("quantity") or 0)
        price = float(pick.get("buy_price") or 0)
        rows.append(
            repo.log_intent(
                wolf_id,
                run_date,
                "birth",
                symbol=str(pick.get("symbol", "")).upper(),
                conviction_score=pick.get("conviction"),
                target_allocation=round(qty * price, 2) if qty and price else None,
                rationale=pick.get("reasoning"),
            )
        )
    return rows


def build_deploy_screen_response(
    deploy_result: dict[str, Any],
    *,
    strategy: str,
    allocation: int,
) -> dict[str, Any]:
    """Shape a legacy /api/bots/deploy `screen` payload for the HTML UI."""
    wolf = deploy_result["wolf"]
    brain = deploy_result["brain"]
    executor = deploy_result["executor"]
    shortlist = deploy_result.get("shortlist") or []

    candidates = []
    for p in brain.get("picks") or []:
        sym = str(p.get("symbol", "")).upper()
        candidates.append(
            {
                "ticker": sym,
                "symbol": sym,
                "name": sym,
                "buyPrice": p.get("buy_price"),
                "target": p.get("target"),
                "stopLoss": p.get("stop_loss"),
                "conviction": p.get("conviction"),
                "reasoning": p.get("reasoning"),
                "verdict": "buy",
            }
        )

    bought = [
        a for a in executor.get("actions_taken", []) if a.get("action") == "BUY"
    ]
    rejected = executor.get("actions_rejected") or []
    if bought:
        action = "executed" if not rejected else "partial"
    else:
        action = "none"

    return {
        "strategy": strategy,
        "supported": True,
        "pipeline": "wolf_brain",
        "screenedCount": len(shortlist),
        "candidates": candidates,
        "llmSummary": brain.get("birth_intent") or "",
        "reasoningLog": [
            {
                "phase": "setup",
                "message": (
                    f"Wolf {wolf['wolf_id']} ({wolf['wolf_name']}) · "
                    f"₹{allocation:,} pool · {strategy}"
                ),
            },
            {
                "phase": "brain",
                "message": (brain.get("birth_intent") or "No birth thesis returned.")[
                    :500
                ],
            },
            {
                "phase": "executor",
                "message": executor.get("summary") or "No trades executed.",
            },
        ],
        "botAction": {
            "action": action,
            "message": executor.get("summary") or "",
            "rejected": rejected,
        },
        "pipelinePayload": brain,
        "executor": executor,
    }


def deploy_new_wolf(
    *,
    user_id: UUID,
    strategy: str,
    budget: int | float,
    guardrails: dict[str, float],
    wolf_name: str | None = None,
    execution_mode: str | None = None,
    as_of: date | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Full Supabase birth deploy: shortlist → brain → executor → DB audit rows.

    Steps match wolf_brain_executor_handover.md Part 3.
    """
    trade_strategy = strategy.lower().strip()
    strategy_code = _STRATEGY_CODES.get(trade_strategy)
    if not strategy_code:
        raise ValueError(f"unknown strategy {strategy!r}")

    run_date = as_of or date.today()

    try:
        sync_dossiers_from_api()
    except Exception as exc:
        log.warning("[DEPLOY] dossier sync failed (continuing): %s", exc)

    shortlist = load_shortlist_resolved(trade_strategy, run_date)
    if not shortlist:
        raise ValueError(
            f"No shortlist for '{trade_strategy}' today. "
            "Run the morning pipeline on data-layer-cron, then redeploy."
        )

    shortlist = apply_live_shortlist_prices(shortlist, run_date=run_date)
    shortlist = enrich_shortlist_with_dossiers(shortlist)

    market_context = _market_context()

    wolf_mode = repo.normalize_execution_workspace(
        execution_mode if execution_mode is not None else repo.get_execution_workspace(user_id)
    )

    wolf_id = repo.allocate_wolf_id()
    name = wolf_name or repo.assign_default_wolf_name(user_id, mode=wolf_mode)
    budget_f = float(budget)

    wolf = repo.create_wolf(
        user_id=user_id,
        wolf_id=wolf_id,
        wolf_name=name,
        strategy_code=strategy_code,
        budget_initial=budget_f,
        guardrails=guardrails,
        mode=wolf_mode,
    )
    log.info(
        "[DEPLOY] created wolf %s strategy=%s budget=₹%.0f shortlist=%d",
        wolf_id,
        strategy_code,
        budget_f,
        len(shortlist),
    )

    brain = run_wolf_brain(
        wolf_id=wolf_id,
        mode="deploy",
        trade_strategy=trade_strategy,
        guardrails=guardrails,
        cash_available=budget_f,
        shortlist=shortlist,
        market_context=market_context,
        as_of=run_date,
    )

    selection_run = repo.save_selection_run(
        wolf_id=wolf_id,
        run_type=RUN_TYPE_BIRTH,
        run_date=run_date,
        shortlist_json=shortlist,
        final_picks_json=brain.get("picks"),
        gemini_raw_response=json.dumps(brain, default=str),
    )
    run_id = int(selection_run["run_id"])

    defer_live_orders = (
        wolf_mode == "live" and not dry_run and not live_orders_allowed()
    )
    if defer_live_orders:
        executor = {
            "wolf_id": wolf_id,
            "executed_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(
                timespec="seconds"
            ),
            "actions_taken": [],
            "actions_rejected": [],
            "cash_before": budget_f,
            "cash_after": budget_f,
            "portfolio_value_before": budget_f,
            "portfolio_value_after": budget_f,
            "guardrail_checks": {},
            "summary": (
                "Live birth orders deferred — the Mac execution agent will "
                "place Zerodha orders at the next live trading run."
            ),
            "deferred_live_birth": True,
        }
        log.info(
            "[DEPLOY] deferred live executor for %s (no Kite session on this host)",
            wolf_id,
        )
    else:
        executor = run_wolf_executor(
            wolf_id,
            executor_mode_from_wolf(wolf_mode),
            sells=[],
            buys=_brain_buys(brain.get("picks") or []),
            cash_available=budget_f,
            holdings=[],
            guardrails=guardrails,
            dry_run=dry_run,
            linked_run_id=run_id,
        )

    repo.patch_selection_run_gate_results(run_id, executor)

    birth_text = str(brain.get("birth_intent") or "").strip()
    if birth_text:
        wolf = repo.set_birth_intent_once(wolf_id, birth_text)

    intent_rows = _log_birth_intents(
        wolf_id, run_date, brain.get("picks") or []
    )

    wolf = repo.get_wolf(wolf_id) or wolf

    return {
        "wolf": wolf,
        "wolf_id": wolf_id,
        "brain": brain,
        "executor": executor,
        "selection_run": selection_run,
        "intents": intent_rows,
        "shortlist": shortlist,
        "shortlist_count": len(shortlist),
        "market_context": market_context,
    }


def complete_deferred_live_birth(
    wolf_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute birth buys for a live wolf that was deployed without local Kite access."""
    wolf = repo.get_wolf(wolf_id)
    if not wolf:
        return {"wolf_id": wolf_id, "skipped": True, "reason": "wolf not found"}
    if repo.normalize_execution_workspace(wolf.get("mode")) != "live":
        return {"wolf_id": wolf_id, "skipped": True, "reason": "not a live wolf"}
    if repo.list_open_holdings(wolf_id):
        return {"wolf_id": wolf_id, "skipped": True, "reason": "already has holdings"}

    selection_run = repo.get_latest_selection_run_for_wolf(
        wolf_id, RUN_TYPE_BIRTH
    )
    if not selection_run:
        return {"wolf_id": wolf_id, "skipped": True, "reason": "no birth run"}

    picks = selection_run.get("final_picks_json") or []
    if isinstance(picks, str):
        picks = json.loads(picks)
    buys = _brain_buys(picks if isinstance(picks, list) else [])
    if not buys:
        return {"wolf_id": wolf_id, "skipped": True, "reason": "no birth picks"}

    run_id = int(selection_run["run_id"])
    guardrails = wolf.get("guardrails") or {}
    budget_f = float(wolf.get("budget_initial") or 0)

    executor = run_wolf_executor(
        wolf_id,
        "real",
        sells=[],
        buys=buys,
        cash_available=budget_f,
        holdings=[],
        guardrails=guardrails,
        dry_run=dry_run,
        linked_run_id=run_id,
    )
    if not dry_run:
        repo.patch_selection_run_gate_results(run_id, executor)

    log.info(
        "[DEPLOY] completed deferred live birth for %s — %s",
        wolf_id,
        executor.get("summary", ""),
    )
    return {"wolf_id": wolf_id, "executor": executor}


def complete_all_deferred_live_births(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """Birth-order catch-up for active live wolves with no holdings yet."""
    results: list[dict[str, Any]] = []
    for wolf in repo.list_active_wolves(mode="live"):
        wolf_id = wolf["wolf_id"]
        if repo.list_open_holdings(wolf_id):
            continue
        try:
            results.append(complete_deferred_live_birth(wolf_id, dry_run=dry_run))
        except Exception as exc:
            log.exception("[DEPLOY] deferred live birth failed for %s", wolf_id)
            results.append({"wolf_id": wolf_id, "error": str(exc)})
    return results

