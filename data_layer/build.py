"""
The orchestrator. Loops the Nifty 200, builds one neutral dossier per stock in
parallel, computes the shared market context once, and writes everything out.

Run:  python -m data_layer.build           (pre-open, full rebuild)
      python -m data_layer.build --close    (post-close snapshot)
      python -m data_layer.build --tickers WIPRO,TCS   (smoke subset)

Fault-tolerant per stock: one ticker failing logs and is skipped; the run
completes and produces valid dossiers for everyone else.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from . import config
from .dossier import Dossier, Meta
from .storage import save_dossier, append_snapshot, init_db
from .fetch.prices import fetch_bars, fetch_index_closes, fetch_latest_value
from .fetch.fundamentals import fetch_fundamentals
from .fetch.fundamentals_ext import enrich_fundamentals
from .fetch.news import fetch_news
from .fetch.events import fetch_events
from .fetch.orderbook import fetch_order_books
from .fetch.bigmoves import prefetch_bigmoves, fetch_big_trades
from .fetch.marketmood import fetch_and_save_market_mood
from .fetch.kite_session import ensure_kite_access_token
from .compute.technicals import compute_technicals
from .compute.chart_shape import compute_chart_shape
from .compute.market_context import compute_market_context


def load_universe() -> list[str]:
    with open(config.UNIVERSE_FILE) as fh:
        data = json.load(fh)
    # accept either ["RELIANCE", ...] or [{"ticker": "RELIANCE"}, ...]
    if data and isinstance(data[0], dict):
        return [d["ticker"] for d in data]
    return list(data)


def build_one(
    ticker: str,
    nifty_closes,
    order_books: dict[str, dict | None],
) -> Dossier | None:
    """Assemble a single stock's neutral dossier. Returns None on hard failure."""
    try:
        bars = fetch_bars(ticker)
        fundamentals = fetch_fundamentals(ticker)
        fundamentals = enrich_fundamentals(ticker, fundamentals)

        d = Dossier()
        d.meta = Meta(
            ticker=ticker,
            name=ticker,
            sector="",
            as_of=date.today().isoformat(),
        )
        d.fundamentals = fundamentals

        if bars:
            d.technicals = compute_technicals(
                bars, nifty_closes=nifty_closes, ticker=ticker
            )
            d.chart_shape = compute_chart_shape(bars, ticker=ticker)

        d.news = fetch_news(ticker, return_6m=d.technicals.return_6m)
        d.events = fetch_events(ticker)

        # Phase A uplift — always set keys (null / empty on failure, never omit)
        d.order_book = order_books.get(ticker.strip().upper())
        try:
            d.big_trades = fetch_big_trades(ticker)
        except Exception as e:
            print(f"[FETCH] bigmoves {ticker} FAILED: {e}")
            d.big_trades = None

        if config.FETCH_DELAY:
            time.sleep(config.FETCH_DELAY)
        return d
    except Exception as e:
        print(f"[build] {ticker} failed: {e}")
        return None


def run(snapshot: str = "pre_open", tickers: list[str] | None = None) -> None:
    init_db()
    universe = tickers if tickers is not None else load_universe()
    print(f"[build] {len(universe)} tickers, snapshot={snapshot}")

    # Market-wide fetches once per run (not per stock)
    ensure_kite_access_token()  # TOTP refresh before order-book quotes
    fetch_and_save_market_mood()
    prefetch_bigmoves()
    order_books = fetch_order_books(universe)

    # shared market data, fetched once
    nifty_closes = fetch_index_closes(config.NIFTY_TICKER)
    vix_value = fetch_latest_value(config.VIX_TICKER)

    dossiers: list[Dossier] = []
    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
        futures = {
            pool.submit(build_one, t, nifty_closes, order_books): t
            for t in universe
        }
        for fut in as_completed(futures):
            d = fut.result()
            if d is not None:
                dossiers.append(d)

    # market context computed once from the finished set, then copied into each
    mc = compute_market_context(
        nifty_closes=nifty_closes,
        vix_value=vix_value,
        above_200_flags=[d.technicals.above_200dma for d in dossiers],
    )
    for d in dossiers:
        d.meta.snapshot = snapshot
        d.market_context.nifty_above_200dma = mc.nifty_above_200dma
        d.market_context.nifty_trend = mc.nifty_trend
        d.market_context.india_vix = mc.india_vix
        d.market_context.vix_regime = mc.vix_regime
        d.market_context.market_breadth_pct_above_200dma = mc.market_breadth_pct_above_200dma
        d.market_context.sector = d.meta.sector or None
        save_dossier(d)
        append_snapshot(d)

    print(f"[build] wrote {len(dossiers)} dossiers to {config.get_dossier_dir()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--close", action="store_true", help="post-close snapshot")
    ap.add_argument(
        "--tickers",
        type=str,
        default="",
        help="comma-separated subset for smoke tests (e.g. WIPRO,TCS)",
    )
    args = ap.parse_args()
    subset = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
    run(
        snapshot="post_close" if args.close else "pre_open",
        tickers=subset,
    )
