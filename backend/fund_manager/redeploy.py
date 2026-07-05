"""Redeploy brain + freed-cash handler."""

from __future__ import annotations

import json
import logging
from datetime import date

from repo_paths import ensure_repo_on_path

ensure_repo_on_path()

from google.genai import types
from selector.config import GEMINI_MODEL
from selector.llm.client import get_client

from fund_manager.breaker import check_and_trip_breaker, ensure_day_baseline
from fund_manager.gates import GateAction, GateResult, is_breaker_tripped, run_gates
from fund_manager.intentions import load_picks_for_bot
from fund_manager.ledger import BotLedger
from fund_manager.prices import get_prices
from fund_manager.schemas import RedeployDecision

import database as db

log = logging.getLogger(__name__)

_PROMPT_PATH = ensure_repo_on_path() / "selector" / "prompts" / "redeploy.txt"
_prompt_cache: str | None = None


def _load_redeploy_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is None:
        _prompt_cache = _PROMPT_PATH.read_text(encoding="utf-8")
    return _prompt_cache


def _unfunded_picks(bot_id: int, strategy: str, run_date: date | str | None = None) -> list[dict]:
    try:
        picks = load_picks_for_bot(bot_id, strategy, run_date)
    except FileNotFoundError:
        return []
    held = BotLedger(bot_id).open_tickers()
    return [p for p in picks if p["ticker"].upper() not in held]


def _call_redeploy_brain(
    bot: dict,
    freed_amount: float,
    closed_ticker: str,
    open_positions: list[dict],
    unfunded: list[dict],
    fresh_quote: float | None,
) -> RedeployDecision:
    payload = {
        "strategy": bot["strategy"],
        "freed_amount_inr": freed_amount,
        "closed_ticker": closed_ticker,
        "fresh_quote": fresh_quote,
        "open_positions": open_positions,
        "unfunded_picks": unfunded,
        "guardrails": {
            "max_deployed_pct": bot["max_deployed_pct"],
            "max_per_stock_pct": bot["max_per_stock_pct"],
            "max_daily_loss_pct": bot["max_daily_loss_pct"],
        },
    }
    client = get_client()
    config = types.GenerateContentConfig(
        system_instruction=_load_redeploy_prompt(),
        response_mime_type="application/json",
        response_schema=RedeployDecision,
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=json.dumps(payload, indent=2),
        config=config,
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, RedeployDecision):
        return parsed
    if response.text:
        return RedeployDecision.model_validate_json(response.text)
    raise RuntimeError("Redeploy brain returned no parseable decision")


def _decision_to_pick(decision: RedeployDecision, unfunded: list[dict]) -> dict | None:
    if decision.action == "hold":
        return None
    if decision.action == "fund_pick":
        if not decision.ticker:
            return None
        for p in unfunded:
            if p["ticker"].upper() == decision.ticker.upper():
                return {**p, "allocation_inr": min(
                    float(p.get("allocation_inr") or 0),
                    float(decision.allocation_inr or p.get("allocation_inr") or 0),
                )}
        return None
    if decision.action == "rebuy":
        if not decision.ticker or not decision.buy_price:
            return None
        return {
            "ticker": decision.ticker.upper(),
            "buy_price": decision.buy_price,
            "stop_loss": decision.stop_loss,
            "sell_target": decision.sell_target,
            "allocation_inr": decision.allocation_inr or decision.shares * decision.buy_price,
            "rationale": decision.rationale,
        }
    return None


def _record_redeploy_note(
    bot_id: int,
    closed_ticker: str,
    freed_amount: float,
    outcome: dict,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    from fund_manager.daily_note import DayJournal
    DayJournal.load(bot_id).add_redeploy(closed_ticker, freed_amount, outcome)


def handle_freed_cash(
    bot_id: int,
    freed_amount: float,
    closed_ticker: str,
    *,
    run_date: date | str | None = None,
    dry_run: bool = False,
) -> dict:
    """Reactive redeploy after a close frees cash. Brain decides; gates enforce."""
    ledger = BotLedger(bot_id)
    bot = ledger.bot()

    if is_breaker_tripped(bot_id):
        outcome = {
            "action": "halted",
            "reason": "Circuit breaker tripped — cash held.",
            "freedAmount": freed_amount,
        }
        _record_redeploy_note(bot_id, closed_ticker, freed_amount, outcome, dry_run=dry_run)
        return outcome

    unfunded = _unfunded_picks(bot_id, bot["strategy"], run_date)
    fresh = get_prices([closed_ticker]).get(closed_ticker.upper())
    positions = [
        {"ticker": p.ticker, "shares": p.shares, "entry": p.entry_price, "ltp": p.ltp}
        for p in ledger.open_positions()
    ]

    try:
        decision = _call_redeploy_brain(
            bot, freed_amount, closed_ticker, positions, unfunded, fresh
        )
    except Exception as exc:
        log.exception("Redeploy brain failed for bot %s", bot_id)
        ledger.append_log("redeploy_failed", str(exc), f"Freed ₹{freed_amount:,.0f} from {closed_ticker}")
        outcome = {"action": "error", "reason": str(exc), "freedAmount": freed_amount}
        _record_redeploy_note(bot_id, closed_ticker, freed_amount, outcome, dry_run=dry_run)
        return outcome

    if decision.action == "hold":
        ledger.append_log(
            "redeploy_hold",
            f"Hold ₹{freed_amount:,.0f} after {closed_ticker} close",
            decision.rationale,
        )
        outcome = {"action": "hold", "decision": decision.model_dump(), "freedAmount": freed_amount}
        _record_redeploy_note(bot_id, closed_ticker, freed_amount, outcome, dry_run=dry_run)
        return outcome

    pick = _decision_to_pick(decision, unfunded)
    if not pick:
        ledger.append_log(
            "redeploy_skip",
            f"Brain chose {decision.action} but no valid pick",
            decision.rationale,
        )
        outcome = {"action": "skip", "decision": decision.model_dump(), "freedAmount": freed_amount}
        _record_redeploy_note(bot_id, closed_ticker, freed_amount, outcome, dry_run=dry_run)
        return outcome

    pick = {**pick, "_strategy": bot["strategy"]}
    result = run_gates(bot_id, pick, skip_execute=dry_run)

    outcome = {
        "action": result.action.value,
        "gate": result.gate,
        "reason": result.reason,
        "decision": decision.model_dump(),
        "freedAmount": freed_amount,
    }
    if result.order:
        outcome["order"] = {
            "ticker": result.order.ticker,
            "shares": result.order.shares,
            "cost": result.order.cost,
        }

    if result.action == GateAction.EXECUTE:
        ledger.append_log("redeploy_executed", result.reason, decision.rationale)
    elif result.action == GateAction.PENDING:
        ledger.append_log("redeploy_pending", result.reason, decision.rationale)
    elif result.action in (GateAction.SKIP, GateAction.HALT):
        ledger.append_log("redeploy_blocked", result.reason, decision.rationale)

    _record_redeploy_note(bot_id, closed_ticker, freed_amount, outcome, dry_run=dry_run)
    return outcome
