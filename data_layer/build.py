"""
The orchestrator. Loops the Nifty 200, builds one neutral dossier per stock in
parallel, computes the shared market context once, and writes everything out.

Run:  python -m data_layer.build           (pre-open, full rebuild)
      python -m data_layer.build --close    (post-close snapshot)
      python -m data_layer.build --tickers WIPRO,TCS   (smoke subset)
      python -m data_layer.build --skip-news  (re-run; keep prior news)

Re-runs merge with existing dossiers on disk — failed fetches no longer wipe
good fundamentals, technicals, or news from a prior build.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from . import config
from .dossier import Dossier, Fundamentals, Technicals
from .storage import load_dossier, save_dossier, append_snapshot, init_db
from .fetch.prices import fetch_bars, fetch_index_closes, fetch_latest_value
from .fetch.fundamentals import fetch_fundamentals
from .fetch.news import try_fetch_news
from .fetch.orderbook import fetch_order_books
from .fetch.kite_session import ensure_kite_access_token
from .compute.technicals import compute_technicals
from .compute.chart_shape import compute_chart_shape
from .compute.market_context import compute_market_context


def _fundamentals_populated(f: Fundamentals) -> bool:
    return f.price is not None


def _technicals_populated(t: Technicals) -> bool:
    return t.rsi_14 is not None or t.above_200dma is not None


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
    *,
    skip_news: bool = False,
) -> Dossier | None:
    """Assemble a single stock's neutral dossier. Returns None on hard failure.

    Re-runs merge with the existing dossier on disk: a failed fetch no longer
    wipes good fundamentals, technicals, or news from a prior build.
    """
    try:
        prior = load_dossier(ticker)
        d = prior if prior else Dossier()
        d.meta.ticker = ticker
        d.meta.as_of = date.today().isoformat()
        if not d.meta.name:
            d.meta.name = ticker

        bars = fetch_bars(ticker)
        fundamentals = fetch_fundamentals(ticker)
        if _fundamentals_populated(fundamentals):
            d.fundamentals = fundamentals
        elif prior is None:
            d.fundamentals = fundamentals

        if bars:
            d.technicals = compute_technicals(
                bars, nifty_closes=nifty_closes, ticker=ticker
            )
            d.chart_shape = compute_chart_shape(bars, ticker=ticker)

        if not skip_news:
            return_6m = d.technicals.return_6m if _technicals_populated(d.technicals) else None
            new_news, fetched_ok = try_fetch_news(ticker, return_6m=return_6m)
            if fetched_ok:
                d.news = new_news
            elif prior is None:
                d.news = new_news

        # Kite order book (null when no token); NSE enrichment removed — yfinance
        # fundamentals + Marketaux news + Gemini scoring cover the pipeline.
        order_book = order_books.get(ticker.strip().upper())
        if order_book is not None:
            d.order_book = order_book

        if config.FETCH_DELAY:
            time.sleep(config.FETCH_DELAY)
        return d
    except Exception as e:
        print(f"[build] {ticker} failed: {e}")
        return None


def run(
    snapshot: str = "pre_open",
    tickers: list[str] | None = None,
    *,
    skip_news: bool = False,
) -> None:
    init_db()
    universe = tickers if tickers is not None else load_universe()
    print(
        f"[build] {len(universe)} tickers, snapshot={snapshot}"
        + (" (skip_news)" if skip_news else "")
    )

    # Kite token for order-book quotes (best-effort; build continues without it)
    ensure_kite_access_token()
    order_books = fetch_order_books(universe)

    # shared market data, fetched once
    nifty_closes = fetch_index_closes(config.NIFTY_TICKER)
    vix_value = fetch_latest_value(config.VIX_TICKER)

    dossiers: list[Dossier] = []
    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
        futures = {
            pool.submit(build_one, t, nifty_closes, order_books, skip_news=skip_news): t
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
    ap.add_argument(
        "--skip-news",
        action="store_true",
        help="reuse prior dossier news; avoids Marketaux quota on re-runs",
    )
    args = ap.parse_args()
    subset = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
    run(
        snapshot="post_close" if args.close else "pre_open",
        tickers=subset,
        skip_news=args.skip_news,
    )
