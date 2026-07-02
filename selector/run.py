"""
The orchestrator: funnel -> score -> final selection -> intentions file.

Run:  python -m selector.run value
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict
from datetime import date

from data_layer.storage import load_all_dossiers

from . import config
from .funnel import run_funnel
from .llm.scoring import score_all
from .llm.final import select_final
from .log_setup import setup_logging

log = logging.getLogger(__name__)


def _build_account() -> dict:
    return {
        "budget_total": config.TEST_BUDGET,
        "cash_available": config.TEST_BUDGET,
        "per_stock_cap_pct": config.PER_STOCK_CAP_PCT,
        "open_positions": [],
    }


def _market_context() -> dict:
    dossiers = load_all_dossiers()
    if not dossiers:
        return {}
    return asdict(dossiers[0].market_context)


def run(strategy: str) -> dict:
    run_start = time.monotonic()
    account = _build_account()
    log.info("=" * 70)
    log.info("STARTING RUN  strategy=%r  budget=₹%s  per_stock_cap=%d%%",
             strategy, account["budget_total"], account["per_stock_cap_pct"])
    log.info("=" * 70)

    market_context = _market_context()
    log.debug("market_context: %s", market_context)

    log.info("--- PHASE 1: math funnel ---")
    t0 = time.monotonic()
    survivors = run_funnel(strategy)
    log.info("phase 1 done in %.1fs: %d survivor(s)", time.monotonic() - t0, len(survivors))

    if not survivors:
        from .schemas import FinalPicks
        log.info("no survivors -- skipping phases 2/3, holding cash by default")
        result = FinalPicks(
            picks=[], skipped=[],
            cash_held_inr=account["cash_available"],
            portfolio_note="No stocks survived the math funnel today; holding cash.",
        )
    else:
        log.info("--- PHASE 2: per-stock LLM scoring ---")
        t0 = time.monotonic()
        scored = score_all(survivors, strategy)
        log.info("phase 2 done in %.1fs", time.monotonic() - t0)

        log.info("--- PHASE 3: final selection ---")
        t0 = time.monotonic()
        result = select_final(scored, account, market_context)
        log.info("phase 3 done in %.1fs: %d pick(s), ₹%.2f cash held",
                 time.monotonic() - t0, len(result.picks), result.cash_held_inr)

    date_str = date.today().isoformat()
    config.INTENTIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.INTENTIONS_DIR / f"{strategy}_{date_str}.json"

    payload = {
        "strategy": strategy,
        "date": date_str,
        "budget": config.TEST_BUDGET,
        "market_context": market_context,
        "result": result.model_dump(),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    log.info("RUN COMPLETE in %.1fs -> %s", time.monotonic() - run_start, out_path)
    _print_summary(strategy, out_path, result)
    return payload


def _print_summary(strategy: str, out_path, result) -> None:
    """Human-readable recap, printed to console AND logged (so it's captured
    in the logs/ file too, not just the console)."""
    lines = [f"=== {strategy} — {out_path.name} ==="]
    if not result.picks:
        lines.append("No picks today. Holding cash.")
    for p in result.picks:
        lines.append(f"  BUY {p.ticker:<12} {p.shares} sh @ ₹{p.buy_price:,.2f} "
                      f"= ₹{p.allocation_inr:,.0f}  (conviction {p.conviction}, "
                      f"stop ₹{p.stop_loss:,.2f}, target ₹{p.sell_target:,.2f})")
        lines.append(f"    -> {p.rationale}")
    if result.skipped:
        lines.append("  Skipped:")
        for s in result.skipped:
            lines.append(f"    {s.ticker:<12} {s.reason}")
    lines.append(f"  Cash held: ₹{result.cash_held_inr:,.0f}")
    lines.append(f"  Note: {result.portfolio_note}")
    lines.append(f"Wrote {out_path}")

    summary = "\n" + "\n".join(lines)
    print(summary)
    for line in lines:
        log.info(line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--verbose", "-v", action="store_true",
                     help="also print DEBUG detail to console (per-ticker funnel checks, "
                          "raw LLM I/O, thesis/signals/risks). The log file under logs/ "
                          "always has full detail regardless of this flag.")
    args = ap.parse_args()

    setup_logging(strategy=args.strategy, verbose=args.verbose)
    run(args.strategy)
