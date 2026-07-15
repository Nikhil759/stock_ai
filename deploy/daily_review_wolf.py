"""Daily review for existing Supabase wolves — Wolf Brain + Executor."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from cache.shortlist_cache import load_shortlist_resolved
from deploy.deploy_wolf import (
    _STRATEGY_CODES,
    _brain_buys,
    _market_context,
)
from deploy.enrich_shortlist import enrich_shortlist_with_dossiers
from deploy.live_prices import apply_live_shortlist_prices
from backend.dossier_sync import sync_dossiers_from_api
from db import repository as repo
from db.repository import RUN_TYPE_DAILY_REVIEW
import wolf_api
from wolf_brain import run_wolf_brain
from wolf_executor import run_wolf_executor

log = logging.getLogger(__name__)

_CODE_TO_STRATEGY = {v: k for k, v in _STRATEGY_CODES.items()}


def _strategy_from_wolf(wolf: dict) -> str:
    code = str(wolf.get("strategy_code", "")).upper()
    return _CODE_TO_STRATEGY.get(code, code.lower())


def _format_birth_intent(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("birth_intent", "text", "body", "portfolio_note", "note"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return json.dumps(raw, default=str)
    return str(raw)


def _days_held(opened_at: Any) -> int:
    if opened_at is None:
        return 0
    if isinstance(opened_at, datetime):
        opened = opened_at.date()
    elif hasattr(opened_at, "date"):
        opened = opened_at.date()
    else:
        try:
            opened = date.fromisoformat(str(opened_at)[:10])
        except ValueError:
            return 0
    return max(0, (date.today() - opened).days)


def build_current_holdings(wolf_id: str) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Open positions with live LTPs for Wolf Brain daily_review input."""
    holdings = repo.list_open_holdings(wolf_id)
    symbols = [str(h["symbol"]).upper() for h in holdings if h.get("symbol")]
    ltps = wolf_api._fetch_ltps(symbols)

    out: list[dict[str, Any]] = []
    for h in holdings:
        sym = str(h["symbol"]).upper()
        qty = int(h.get("quantity") or 0)
        if qty <= 0:
            continue
        entry = float(h.get("avg_buy_price") or 0)
        ltp = ltps.get(sym) or entry
        target = float(h.get("sell_target") or 0)
        stop = float(h.get("stop_loss") or 0)
        unrealized = round((ltp - entry) / entry * 100, 2) if entry > 0 else 0.0
        out.append(
            {
                "symbol": sym,
                "quantity": qty,
                "avg_buy_price": round(entry, 2),
                "current_price": round(ltp, 2),
                "target": round(target, 2),
                "stop_loss": round(stop, 2),
                "unrealized_pl_pct": unrealized,
                "days_held": _days_held(h.get("opened_at")),
            }
        )
    return out, ltps


def _brain_sells(
    brain: dict,
    holdings: list[dict],
) -> list[dict[str, Any]]:
    held_qty = {str(h["symbol"]).upper(): int(h["quantity"]) for h in holdings}
    sells: list[dict[str, Any]] = []
    for review in brain.get("holdings_review") or []:
        if review.get("verdict") != "sell":
            continue
        sym = str(review.get("symbol", "")).upper()
        qty = held_qty.get(sym, 0)
        if qty > 0:
            sells.append({"symbol": sym, "quantity": qty})
    return sells


def _log_daily_intents(
    wolf_id: str,
    run_date: date,
    brain: dict,
    executor: dict,
) -> list[dict]:
    rows: list[dict] = []
    current = str(brain.get("current_intent") or "").strip()
    daily = str(brain.get("daily_update") or "").strip()
    summary = str(executor.get("summary") or "").strip()
    rationale_parts = [p for p in (current, daily) if p]
    if summary:
        rationale_parts.append(f"Executor: {summary}")
    portfolio_rationale = "\n\n".join(rationale_parts) or "Daily review completed."

    rows.append(
        repo.log_intent(
            wolf_id,
            run_date,
            "eod",
            rationale=portfolio_rationale,
        )
    )

    for review in brain.get("holdings_review") or []:
        if review.get("verdict") != "sell":
            continue
        sym = str(review.get("symbol", "")).upper()
        rows.append(
            repo.log_intent(
                wolf_id,
                run_date,
                "adjustment",
                symbol=sym,
                rationale=f"Strategic sell: {review.get('reasoning', '')}",
            )
        )

    for pick in brain.get("new_picks") or []:
        sym = str(pick.get("symbol", "")).upper()
        rows.append(
            repo.log_intent(
                wolf_id,
                run_date,
                "adjustment",
                symbol=sym,
                conviction_score=pick.get("conviction"),
                target_allocation=round(
                    int(pick.get("quantity") or 0) * float(pick.get("buy_price") or 0),
                    2,
                ),
                rationale=f"New buy: {pick.get('reasoning', '')}",
            )
        )

    return rows


def _brain_stage_detail(brain: dict) -> str:
    reviews = brain.get("holdings_review") or []
    holds = sum(1 for r in reviews if r.get("verdict") == "hold")
    sells = sum(1 for r in reviews if r.get("verdict") == "sell")
    picks = len(brain.get("new_picks") or [])
    return f"{holds} hold, {sells} sell, {picks} new pick(s)"


def run_daily_review_for_wolf(
    wolf_id: str,
    *,
    as_of: date | None = None,
    dry_run: bool = False,
    health_run_id: str | None = None,
) -> dict[str, Any]:
    """
    Full daily review for one wolf. Optionally tracks stages in fund_manager_runs.
    """
    from fund_manager_health import (
        finalize_wolf_run,
        start_wolf_run,
        update_wolf_stage,
    )

    wolf = repo.get_wolf(wolf_id)
    if wolf is None:
        return {"error": "Wolf not found", "wolf_id": wolf_id}

    status = wolf.get("status")
    if status == "closed":
        return {
            "skipped": True,
            "reason": "closed",
            "wolf_id": wolf_id,
            "message": "Wolf is closed.",
        }
    if status == "paused":
        return {
            "skipped": True,
            "reason": "paused",
            "wolf_id": wolf_id,
            "message": "Paused — daily review skipped.",
        }

    run_date = as_of or date.today()
    trade_strategy = _strategy_from_wolf(wolf)
    guardrails = wolf.get("guardrails") or {}
    if isinstance(guardrails, str):
        guardrails = json.loads(guardrails)
    cash = float(wolf.get("budget_available") or 0)
    mode = str(wolf.get("mode") or "paper")
    birth_intent = _format_birth_intent(wolf.get("birth_intent"))

    fm_id = health_run_id
    if not fm_id and not dry_run:
        try:
            fm_row = start_wolf_run(wolf_id, run_date=run_date)
            fm_id = fm_row["id"]
        except Exception:
            log.exception("[DAILY REVIEW] failed to start health run for %s", wolf_id)

    def _stage(key: str, st: str, detail: str) -> None:
        if fm_id and not dry_run:
            try:
                update_wolf_stage(fm_id, key, status=st, detail=detail)
            except Exception:
                log.exception("[DAILY REVIEW] stage update failed %s", key)

    result: dict[str, Any] = {
        "wolf_id": wolf_id,
        "run_date": run_date.isoformat(),
        "dry_run": dry_run,
        "fund_manager_run_id": fm_id,
    }

    try:
        try:
            sync_dossiers_from_api()
        except Exception as exc:
            log.warning("[DAILY REVIEW] dossier sync failed (continuing): %s", exc)

        shortlist = load_shortlist_resolved(trade_strategy, run_date)
        if not shortlist:
            msg = f"No shortlist for '{trade_strategy}' on {run_date}"
            _stage("shortlist", "failed", msg)
            if fm_id and not dry_run:
                finalize_wolf_run(fm_id, overall_status="failed", error_detail=msg)
            result.update({"error": msg, "failed_stage": "shortlist"})
            return result

        shortlist = apply_live_shortlist_prices(shortlist, run_date=run_date)
        shortlist = enrich_shortlist_with_dossiers(shortlist)
        _stage("shortlist", "success", f"{len(shortlist)} candidates ({trade_strategy})")

        current_holdings, ltps = build_current_holdings(wolf_id)
        _stage(
            "holdings",
            "success",
            f"{len(current_holdings)} open position(s), LTPs refreshed",
        )

        market_context = _market_context()
        brain = run_wolf_brain(
            wolf_id=wolf_id,
            mode="daily_review",
            trade_strategy=trade_strategy,
            guardrails=guardrails,
            cash_available=cash,
            shortlist=shortlist,
            market_context=market_context,
            current_holdings=current_holdings,
            birth_intent=birth_intent,
            as_of=run_date,
        )
        _stage("brain", "success", _brain_stage_detail(brain))

        sells = _brain_sells(brain, current_holdings)
        buys = _brain_buys(brain.get("new_picks") or [])
        fill_prices = dict(ltps)
        for pick in brain.get("new_picks") or []:
            sym = str(pick.get("symbol", "")).upper()
            bp = float(pick.get("buy_price") or 0)
            if sym and bp > 0:
                fill_prices[sym] = bp

        selection_run: dict | None = None
        run_id: int | None = None
        if not dry_run:
            selection_run = repo.save_selection_run(
                wolf_id=wolf_id,
                run_type=RUN_TYPE_DAILY_REVIEW,
                run_date=run_date,
                shortlist_json=shortlist,
                final_picks_json={
                    "holdings_review": brain.get("holdings_review"),
                    "new_picks": brain.get("new_picks"),
                },
                gemini_raw_response=json.dumps(brain, default=str),
            )
            run_id = int(selection_run["run_id"])

        executor = run_wolf_executor(
            wolf_id,
            mode,
            sells=sells,
            buys=buys,
            guardrails=guardrails,
            fill_prices=fill_prices,
            dry_run=dry_run,
            linked_run_id=run_id,
        )

        if run_id and not dry_run:
            repo.patch_selection_run_gate_results(run_id, executor)

        exec_detail = executor.get("summary") or "No trades executed."
        rejected = executor.get("actions_rejected") or []
        exec_status = "success" if not rejected else "partial"
        _stage("executor", exec_status, exec_detail[:500])

        if not dry_run:
            intents = _log_daily_intents(wolf_id, run_date, brain, executor)
            _stage("intents", "success", f"{len(intents)} intent row(s) logged")
            result["intents"] = intents

        overall = "success"
        if rejected:
            overall = "partial"
        if fm_id and not dry_run:
            finalize_wolf_run(
                fm_id,
                overall_status=overall,
                selection_run_id=run_id,
            )

        result.update(
            {
                "brain": brain,
                "executor": executor,
                "selection_run": selection_run,
                "shortlist_count": len(shortlist),
                "overall_status": overall,
            }
        )
        log.info(
            "[DAILY REVIEW] %s done — %s",
            wolf_id,
            _brain_stage_detail(brain),
        )
        return result

    except Exception as e:
        log.exception("[DAILY REVIEW] failed for %s", wolf_id)
        if fm_id and not dry_run:
            try:
                finalize_wolf_run(
                    fm_id,
                    overall_status="failed",
                    error_detail=str(e)[:500],
                )
            except Exception:
                log.exception("[DAILY REVIEW] finalize failed for %s", wolf_id)
        result.update({"error": str(e), "failed": True})
        return result


def run_daily_review_all_wolves(
    *,
    dry_run: bool = False,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    wolves = repo.list_active_wolves()
    if not wolves:
        log.info("[DAILY REVIEW] no active wolves")
        return []

    results: list[dict[str, Any]] = []
    for wolf in wolves:
        wolf_id = wolf["wolf_id"]
        log.info("[DAILY REVIEW] starting %s", wolf_id)
        try:
            results.append(
                run_daily_review_for_wolf(
                    wolf_id,
                    as_of=as_of,
                    dry_run=dry_run,
                )
            )
        except Exception:
            log.exception("[DAILY REVIEW] unhandled failure for %s", wolf_id)
            results.append({"wolf_id": wolf_id, "error": "daily review failed"})
    return results


def print_daily_review_summary(result: dict[str, Any]) -> None:
    wolf_id = result.get("wolf_id") or "?"
    print(f"\n=== Daily review — Wolf {wolf_id} ===")
    if result.get("skipped"):
        print(f"Skipped: {result.get('reason')} — {result.get('message', '')}")
        return
    if result.get("error"):
        print(f"Error: {result['error']}")
        return
    brain = result.get("brain") or {}
    print(f"Intent: {brain.get('current_intent', '')[:120]}")
    print(f"Executor: {(result.get('executor') or {}).get('summary', '')[:200]}")
