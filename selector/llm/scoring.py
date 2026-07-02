"""
Phase 2 — per-stock scoring. One Gemini call per survivor, run in parallel.

Run:  python -m selector.llm.scoring value --limit 5
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..schemas import StockVerdict
from . import client

log = logging.getLogger(__name__)


def _skip(ticker: str, reason: str) -> StockVerdict:
    log.debug("%-12s -> skip (%s)", ticker, reason)
    return StockVerdict(
        ticker=ticker,
        decision="skip",
        conviction=0,
        buy_price=0,
        stop_loss=0,
        sell_target=0,
        thesis=f"Scoring failed, defaulted to skip: {reason}",
        risks=[],
        key_signals=[],
    )


def score_stock(dossier, strategy: str) -> StockVerdict:
    """Score one dossier against one strategy. Never raises -- any failure
    (bad JSON, absurd price, network error) degrades to a "skip" verdict."""
    ticker = dossier.meta.ticker
    t0 = time.monotonic()
    try:
        user_content = dossier.to_json(indent=0)
        log.debug("%-12s dossier price=%s -- sending to LLM", ticker, dossier.fundamentals.price)
        response = client.generate_structured(strategy, user_content, StockVerdict)

        verdict = response.parsed
        if verdict is None:
            # response_schema validation failed inside the SDK -- try a raw parse
            log.warning("%-12s response.parsed was None, falling back to raw JSON parse", ticker)
            verdict = StockVerdict.model_validate_json(response.text)

        if verdict.ticker != ticker:
            log.warning("%-12s LLM returned ticker %r, correcting to %r", ticker, verdict.ticker, ticker)
            verdict = verdict.model_copy(update={"ticker": ticker})

        if verdict.decision != "skip":
            prices = (verdict.buy_price, verdict.stop_loss, verdict.sell_target)
            if any(p is None or p <= 0 for p in prices):
                return _skip(ticker, f"non-positive price in a {verdict.decision!r} verdict: {prices}")
            price = dossier.fundamentals.price
            if price and price > 0 and not (0.5 * price <= verdict.buy_price <= 2.0 * price):
                return _skip(ticker, f"buy_price {verdict.buy_price} is wildly off dossier price {price}")

        elapsed = time.monotonic() - t0
        log.info("%-12s -> %-5s conviction=%-3d buy=%-10s stop=%-10s target=%-10s (%.1fs)",
                 ticker, verdict.decision, verdict.conviction,
                 verdict.buy_price, verdict.stop_loss, verdict.sell_target, elapsed)
        log.debug("%-12s thesis: %s", ticker, verdict.thesis)
        if verdict.key_signals:
            log.debug("%-12s signals: %s", ticker, verdict.key_signals)
        if verdict.risks:
            log.debug("%-12s risks: %s", ticker, verdict.risks)
        return verdict
    except Exception as e:
        log.warning("%-12s scoring failed: %s", ticker, e)
        return _skip(ticker, str(e))


def score_all(survivors: list, strategy: str, max_workers: int = 8) -> list[StockVerdict]:
    log.info("scoring %d survivor(s) for strategy=%r (%d parallel workers)",
             len(survivors), strategy, max_workers)
    t0 = time.monotonic()
    verdicts: list[StockVerdict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(score_stock, d, strategy): d for d in survivors}
        for fut in as_completed(futures):
            verdicts.append(fut.result())
    elapsed = time.monotonic() - t0

    buys = sum(1 for v in verdicts if v.decision == "buy")
    watches = sum(1 for v in verdicts if v.decision == "watch")
    skips = sum(1 for v in verdicts if v.decision == "skip")
    log.info("scoring done in %.1fs: %d buy, %d watch, %d skip (of %d)",
             elapsed, buys, watches, skips, len(verdicts))
    return verdicts


if __name__ == "__main__":
    from ..funnel import run_funnel
    from ..log_setup import setup_logging

    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--verbose", "-v", action="store_true", help="show thesis/signals/risks and raw LLM I/O")
    args = ap.parse_args()

    setup_logging(strategy=args.strategy, verbose=args.verbose)
    survivors = run_funnel(args.strategy)[: args.limit]
    verdicts = score_all(survivors, args.strategy)
    verdicts.sort(key=lambda v: -v.conviction)
    for v in verdicts:
        print(json.dumps(v.model_dump(), indent=2))
