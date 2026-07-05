"""
The orchestrator: funnel -> score -> final selection -> intentions file.

Run:  python -m selector.run value
"""
from __future__ import annotations

import argparse
import logging

from .log_setup import setup_logging
from .pipeline import run_pipeline
from .schemas import FinalPicks

log = logging.getLogger(__name__)


def run(strategy: str) -> dict:
    payload = run_pipeline(strategy, write_intentions=True)
    result = FinalPicks.model_validate(payload["result"])
    _print_summary(strategy, payload.get("intentionsPath", ""), result)
    return payload


def _print_summary(strategy: str, out_path, result) -> None:
    """Human-readable recap, printed to console AND logged (so it's captured
    in the logs/ file too, not just the console)."""
    lines = [f"=== {strategy} — {out_path or 'no file'} ==="]
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
    if out_path:
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
