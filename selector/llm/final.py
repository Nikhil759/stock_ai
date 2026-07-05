"""
Phase 3 — final selection. One Gemini call: rank the scored survivors,
allocate the budget, pick 1-3. Prices are frozen from Phase 2 -- this call
ranks and allocates only, never invents a new buy/stop/target.
"""
from __future__ import annotations

import json
import logging
import math
import time

from ..config import PER_STOCK_CAP_PCT
from ..reasoning_log import ReasoningLog
from ..schemas import FinalPicks, Pick, SkippedEntry, StockVerdict
from . import client

log = logging.getLogger(__name__)


def _validate_and_clamp(
    result: FinalPicks,
    account: dict,
    *,
    per_stock_cap_pct: float | None = None,
) -> FinalPicks:
    """Enforce the budget/cap constraints the LLM was asked to respect but
    might not have honored exactly. Clamp or drop violations, log every
    change, recompute cash_held_inr from what actually survives."""
    budget_total = account["budget_total"]
    cash_available = account["cash_available"]
    cap_pct = per_stock_cap_pct if per_stock_cap_pct is not None else account.get("per_stock_cap_pct", PER_STOCK_CAP_PCT)
    per_stock_cap = budget_total * cap_pct / 100

    log.debug("validating LLM's %d raw pick(s) against per_stock_cap=₹%.0f cash_available=₹%.0f",
              len(result.picks), per_stock_cap, cash_available)

    kept: list[Pick] = []
    dropped: list[SkippedEntry] = list(result.skipped)

    # sort by conviction desc so, if we later have to trim for budget, we
    # trim the weakest picks first
    for p in sorted(result.picks, key=lambda p: -p.conviction):
        log.debug("  raw pick %-12s conviction=%d shares=%s buy_price=%s allocation_inr=%s",
                  p.ticker, p.conviction, p.shares, p.buy_price, p.allocation_inr)
        if p.buy_price is None or p.buy_price <= 0:
            dropped.append(SkippedEntry(ticker=p.ticker, reason=f"invalid buy_price {p.buy_price}"))
            log.warning("dropped %s: invalid buy_price %s", p.ticker, p.buy_price)
            continue

        shares = int(p.shares)  # whole shares only, per spec
        allocation = round(shares * p.buy_price, 2)

        if allocation > per_stock_cap:
            shares = math.floor(per_stock_cap / p.buy_price)
            allocation = round(shares * p.buy_price, 2)
            log.warning("clamped %s: allocation exceeded per-stock cap "
                        "(%d%% of budget = ₹%.0f), reduced to %d shares (₹%.2f)",
                        p.ticker, cap_pct, per_stock_cap, shares, allocation)
            if shares < 1:
                dropped.append(SkippedEntry(ticker=p.ticker, reason="cannot afford even 1 share within per-stock cap"))
                continue

        kept.append(p.model_copy(update={"shares": shares, "allocation_inr": allocation}))

    # total budget check: if the LLM over-allocated across picks combined,
    # drop the weakest (already sorted by -conviction) until it fits
    total = sum(p.allocation_inr for p in kept)
    while total > cash_available and kept:
        worst = kept.pop()  # lowest conviction, since kept is sorted desc
        dropped.append(SkippedEntry(ticker=worst.ticker, reason="dropped to stay within total cash_available"))
        log.warning("dropped %s: total allocation ₹%.2f exceeded cash_available ₹%.2f",
                    worst.ticker, total, cash_available)
        total = sum(p.allocation_inr for p in kept)

    cash_held = round(cash_available - total, 2)
    log.info("final validation: %d pick(s) kept, %d dropped/skipped, total allocated=₹%.2f, cash_held=₹%.2f",
             len(kept), len(dropped), total, cash_held)
    return FinalPicks(
        picks=kept,
        skipped=dropped,
        cash_held_inr=cash_held,
        portfolio_note=result.portfolio_note,
    )


def select_final(
    scored: list[StockVerdict],
    account: dict,
    market_context: dict,
    *,
    per_stock_cap_pct: float | None = None,
    reasoning: ReasoningLog | None = None,
) -> FinalPicks:
    survivors = [v for v in scored if v.decision in ("buy", "watch")]
    skipped_early = [
        SkippedEntry(ticker=v.ticker, reason=f"per-stock decision: {v.decision}")
        for v in scored if v.decision == "skip"
    ]
    log.info("final selection input: %d candidate(s) (buy/watch), %d already skipped at per-stock stage",
             len(survivors), len(skipped_early))
    for v in sorted(survivors, key=lambda v: -v.conviction):
        log.debug("  candidate %-12s decision=%-5s conviction=%d", v.ticker, v.decision, v.conviction)

    if not survivors:
        log.info("no buy/watch candidates -- holding cash by default, no LLM call made")
        if reasoning is not None:
            reasoning.add("final", "No buy/watch survivors after per-stock scoring — holding cash.")
        return FinalPicks(
            picks=[],
            skipped=skipped_early,
            cash_held_inr=account["cash_available"],
            portfolio_note="Nothing cleared the bar in per-stock scoring today; holding cash.",
        )

    user_content = json.dumps({
        "account": account,
        "market_context": market_context,
        "survivors": [v.model_dump() for v in survivors],
    }, default=str)

    t0 = time.monotonic()
    try:
        response = client.generate_final(user_content, FinalPicks)
        result = response.parsed
        if result is None:
            log.warning("response.parsed was None for final selection, falling back to raw JSON parse")
            result = FinalPicks.model_validate_json(response.text)
    except Exception as e:
        log.error("final selection LLM call failed (%s); holding cash as a safe default", e)
        return FinalPicks(
            picks=[],
            skipped=skipped_early,
            cash_held_inr=account["cash_available"],
            portfolio_note=f"Final selection failed ({e}); holding cash as a safe default.",
        )
    elapsed = time.monotonic() - t0
    log.info("final selection LLM call returned in %.1fs: %d raw pick(s) proposed", elapsed, len(result.picks))
    if reasoning is not None:
        if result.picks:
            picks_line = "; ".join(
                f"{p.ticker} ({p.shares} sh, conviction {p.conviction})" for p in result.picks
            )
            reasoning.add(
                "final",
                f"Portfolio LLM chose {len(result.picks)} pick(s): {picks_line}.",
                portfolioNote=result.portfolio_note,
            )
        else:
            reasoning.add("final", f"No picks allocated — {result.portfolio_note}")
        note_snip = (result.portfolio_note or "").replace("\n", " ")[:200]
        if note_snip:
            reasoning.add("final", f"Portfolio note: {note_snip}", portfolioNote=result.portfolio_note)

    result.skipped = skipped_early + result.skipped
    validated = _validate_and_clamp(result, account, per_stock_cap_pct=per_stock_cap_pct)
    if reasoning is not None and validated.picks:
        reasoning.add(
            "final",
            f"After budget validation: {len(validated.picks)} pick(s), ₹{validated.cash_held_inr:,.0f} cash held.",
            picks=[p.model_dump() for p in validated.picks],
        )
    return validated
