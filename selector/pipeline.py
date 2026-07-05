"""
Reusable dossier → funnel → LLM pipeline.

Used by `selector.run` (CLI), per-Wolf cron, and `backend.dossier_screen` (deploy API).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import date

from data_layer.storage import load_all_dossiers

from .config import INTENTIONS_DIR, PER_STOCK_CAP_PCT, TEST_BUDGET
from .funnel import run_funnel
from .llm.scoring import score_all
from .llm.final import select_final
from .reasoning_log import ReasoningLog
from .schemas import FinalPicks, StockVerdict

log = logging.getLogger(__name__)


def _market_context() -> dict:
    dossiers = load_all_dossiers()
    if not dossiers:
        return {}
    return asdict(dossiers[0].market_context)


def run_pipeline(
    strategy: str,
    *,
    budget: int | None = None,
    cash_available: float | None = None,
    per_stock_cap_pct: float | None = None,
    use_llm: bool = True,
    write_intentions: bool = False,
    reasoning: ReasoningLog | None = None,
    bot_id: int | None = None,
    wolf_context: dict | None = None,
    open_positions: list[dict] | None = None,
) -> dict:
    """Run dossier-based screening for one strategy or one Wolf.

    When bot_id is set, writes intentions/wolf_<id>_<date>.json and uses
    wolf_context for history-aware final selection.
    """
    budget = budget if budget is not None else TEST_BUDGET
    cash = cash_available if cash_available is not None else float(budget)
    cap_pct = per_stock_cap_pct if per_stock_cap_pct is not None else PER_STOCK_CAP_PCT
    trail = reasoning or ReasoningLog()

    all_dossiers = load_all_dossiers()
    dossier_count = len(all_dossiers)
    if dossier_count == 0:
        raise FileNotFoundError(
            "No dossiers found. Run `python -m data_layer.build` from the repo root first."
        )

    as_of = all_dossiers[0].meta.as_of if all_dossiers else ""
    trail.add(
        "dossiers",
        f"Loaded {dossier_count} dossiers (as of {as_of or 'unknown'}) — pre-built facts, no live fetch.",
        dossierCount=dossier_count,
        asOf=as_of,
    )

    positions = open_positions if open_positions is not None else []
    if wolf_context and not positions:
        positions = wolf_context.get("openPositions") or []

    account = {
        "budget_total": budget,
        "cash_available": cash,
        "per_stock_cap_pct": cap_pct,
        "open_positions": positions,
    }

    run_start = time.monotonic()
    market_context = _market_context()

    log.info(
        "pipeline start strategy=%r bot_id=%s dossiers=%d budget=₹%s",
        strategy, bot_id, dossier_count, budget,
    )

    survivors = run_funnel(strategy, reasoning=trail)
    funnel_count = len(survivors)

    scored: list[StockVerdict] = []
    if not survivors:
        result = FinalPicks(
            picks=[],
            skipped=[],
            cash_held_inr=cash,
            portfolio_note="No stocks survived the math funnel today; holding cash.",
        )
        trail.add("funnel", "No stocks passed the math funnel — holding cash.")
    elif not use_llm:
        result = FinalPicks(
            picks=[],
            skipped=[],
            cash_held_inr=cash,
            portfolio_note=f"{funnel_count} stock(s) passed the math funnel (LLM skipped).",
        )
        trail.add("scoring", f"LLM skipped — {funnel_count} funnel survivor(s) only.")
    else:
        scored = score_all(survivors, strategy, reasoning=trail)
        result = select_final(
            scored,
            account,
            market_context,
            per_stock_cap_pct=cap_pct,
            reasoning=trail,
            wolf_context=wolf_context,
        )

    trail.add(
        "shortlist",
        f"{len(result.picks)} final pick(s), ₹{result.cash_held_inr:,.0f} cash held.",
        pickCount=len(result.picks),
        cashHeld=result.cash_held_inr,
    )

    date_str = date.today().isoformat()
    payload = {
        "strategy": strategy,
        "date": date_str,
        "budget": budget,
        "botId": bot_id,
        "dossierCount": dossier_count,
        "funnelSurvivors": funnel_count,
        "market_context": market_context,
        "wolfContext": wolf_context,
        "result": result.model_dump(),
        "scoringSummary": [v.model_dump() for v in scored],
        "reasoningLog": trail.entries(),
        "elapsedSec": round(time.monotonic() - run_start, 1),
    }

    if write_intentions:
        INTENTIONS_DIR.mkdir(parents=True, exist_ok=True)
        if bot_id is not None:
            out_path = INTENTIONS_DIR / f"wolf_{bot_id}_{date_str}.json"
        else:
            out_path = INTENTIONS_DIR / f"{strategy}_{date_str}.json"
        out_path.write_text(json.dumps(payload, indent=2, default=str))
        payload["intentionsPath"] = str(out_path)
        log.info("wrote intentions -> %s", out_path)

    log.info(
        "pipeline done in %.1fs: %d funnel survivors -> %d pick(s)",
        payload["elapsedSec"],
        funnel_count,
        len(result.picks),
    )
    return payload
