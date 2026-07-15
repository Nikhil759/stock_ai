"""
Wolf Brain — LLM judgment layer (deploy + daily review).

Reads a pre-scored strategy shortlist; proposes buys/sells. Does not execute
trades or compute final cash — Wolf Executor does that deterministically.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Literal

from wolf_brain.schemas import (
    BrainPick,
    DailyReviewBrainOutput,
    DeployBrainOutput,
    HoldingReview,
)
from wolf_brain.validate import (
    normalize_guardrails,
    validate_daily_review_output,
    validate_deploy_output,
)

log = logging.getLogger(__name__)


def _shortlist_symbols(shortlist: list[dict]) -> set[str]:
    return {
        str(c.get("symbol", "")).strip().upper()
        for c in shortlist
        if c.get("symbol")
    }


def _held_symbols(holdings: list[dict] | None) -> set[str]:
    if not holdings:
        return set()
    return {
        str(h.get("symbol", "")).strip().upper()
        for h in holdings
        if h.get("symbol")
    }


def _format_birth_intent(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        for key in ("birth_intent", "text", "portfolio_note", "note"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return json.dumps(raw, default=str)
    return str(raw)


def _call_gemini(mode: str, payload: dict, schema: type) -> Any:
    from wolf_brain.gemini import generate_brain

    user_content = json.dumps(payload, default=str)
    response = generate_brain(mode=mode, user_content=user_content, response_schema=schema)
    parsed = response.parsed
    if parsed is not None:
        return parsed
    log.warning("[WOLF BRAIN] response.parsed was None; falling back to JSON parse")
    return schema.model_validate_json(response.text or "{}")


def _empty_deploy(
    *,
    wolf_id: str,
    trade_strategy: str,
    reason: str,
) -> dict:
    return DeployBrainOutput(
        birth_intent=(
            f"{trade_strategy.title()} wolf {wolf_id} deploy on {date.today().isoformat()}. "
            f"{reason}"
        ),
        picks=[],
    ).model_dump()


def _empty_daily_review(
    *,
    holdings: list[dict] | None,
    reason: str,
    birth_intent: str | None,
) -> dict:
    reviews = [
        HoldingReview(
            symbol=str(h["symbol"]).upper(),
            verdict="hold",
            reasoning="No shortlist or LLM unavailable; maintaining position.",
        )
        for h in (holdings or [])
        if h.get("symbol")
    ]
    anchor = (birth_intent or "Original thesis unchanged.")[:200]
    return DailyReviewBrainOutput(
        holdings_review=reviews,
        new_picks=[],
        current_intent=f"Hold and monitor — {anchor}",
        daily_update=reason,
    ).model_dump()


def run_wolf_brain(
    *,
    wolf_id: str,
    mode: Literal["deploy", "daily_review"],
    trade_strategy: str,
    guardrails: dict,
    cash_available: float,
    shortlist: list[dict],
    market_context: dict,
    current_holdings: list[dict] | None = None,
    birth_intent: Any = None,
    as_of: date | None = None,
) -> dict:
    """
    Run Wolf Brain for deploy (initial picks + birth thesis) or daily_review.

    Caller must supply cash_available, shortlist, and (for daily) holdings —
    never estimated inside this function.
    """
    if mode not in ("deploy", "daily_review"):
        raise ValueError(f"invalid mode {mode!r}; expected 'deploy' or 'daily_review'")

    day = (as_of or date.today()).isoformat()
    norm_guardrails = normalize_guardrails(guardrails)
    symbols = _shortlist_symbols(shortlist)
    held = _held_symbols(current_holdings)
    birth_text = _format_birth_intent(birth_intent)

    log.info(
        "[WOLF BRAIN] run wolf_id=%s mode=%s strategy=%s cash=₹%.0f shortlist=%d holdings=%d",
        wolf_id,
        mode,
        trade_strategy,
        cash_available,
        len(shortlist),
        len(held),
    )

    base_payload: dict[str, Any] = {
        "wolf_id": wolf_id,
        "mode": mode,
        "trade_strategy": trade_strategy.lower().strip(),
        "as_of": day,
        "guardrails": norm_guardrails,
        "cash_available": round(float(cash_available), 2),
        "shortlist": shortlist,
        "market_context": market_context or {},
    }

    if mode == "deploy":
        if not shortlist:
            return _empty_deploy(
                wolf_id=wolf_id,
                trade_strategy=trade_strategy,
                reason="No scored shortlist available for today; holding cash.",
            )
        try:
            raw = _call_gemini("deploy", base_payload, DeployBrainOutput)
            validated = validate_deploy_output(
                raw,
                cash_available=cash_available,
                guardrails=norm_guardrails,
                shortlist_symbols=symbols,
            )
            log.info(
                "[WOLF BRAIN] deploy done: %d pick(s), birth_intent %d chars",
                len(validated.picks),
                len(validated.birth_intent),
            )
            return validated.model_dump()
        except Exception as e:
            log.exception("[WOLF BRAIN] deploy LLM failed")
            return _empty_deploy(
                wolf_id=wolf_id,
                trade_strategy=trade_strategy,
                reason=f"Brain unavailable ({e}); no deploy picks.",
            )

    # daily_review
    payload = {
        **base_payload,
        "current_holdings": current_holdings or [],
        "birth_intent": birth_text or "",
    }
    if not shortlist and not held:
        return _empty_daily_review(
            holdings=current_holdings,
            reason="No shortlist and no holdings; nothing to review today.",
            birth_intent=birth_text,
        )
    try:
        raw = _call_gemini("daily_review", payload, DailyReviewBrainOutput)
        validated = validate_daily_review_output(
            raw,
            cash_available=cash_available,
            guardrails=norm_guardrails,
            shortlist_symbols=symbols,
            held_symbols=held,
        )
        log.info(
            "[WOLF BRAIN] daily_review done: %d holding verdict(s), %d new pick(s)",
            len(validated.holdings_review),
            len(validated.new_picks),
        )
        return validated.model_dump()
    except Exception as e:
        log.exception("[WOLF BRAIN] daily_review LLM failed")
        return _empty_daily_review(
            holdings=current_holdings,
            reason=f"Daily review failed ({e}); defaulting to hold on open positions.",
            birth_intent=birth_text,
        )
