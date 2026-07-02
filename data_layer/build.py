"""
The orchestrator. Loops the Nifty 200, builds one neutral dossier per stock in
parallel, computes the shared market context once, and writes everything out.

Run:  python -m data_layer.build           (pre-open, full rebuild)
      python -m data_layer.build --close    (post-close snapshot)

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


def build_one(ticker: str, nifty_closes) -> Dossier | None:
    """Assemble a single stock's neutral dossier. Returns None on hard failure."""
    try:
        bars = fetch_bars(ticker)
        fundamentals = fetch_fundamentals(ticker)
        fundamentals = enrich_fundamentals(ticker, fundamentals)  # Phase 2 (noop for now)

        d = Dossier()
        d.meta = Meta(
            ticker=ticker,
            name=ticker,                       # replace with real name if you fetch it
            sector="",                         # set in Phase 2
            as_of=date.today().isoformat(),
        )
        d.fundamentals = fundamentals

        if bars:
            d.technicals = compute_technicals(bars, nifty_closes=nifty_closes)
            d.chart_shape = compute_chart_shape(bars)

        d.news = fetch_news(ticker, return_6m=d.technicals.return_6m)  # Phase 3 (noop)
        d.events = fetch_events(ticker)                                # Phase 3 (noop)

        if config.FETCH_DELAY:
            time.sleep(config.FETCH_DELAY)
        return d
    except Exception as e:
        print(f"[build] {ticker} failed: {e}")
        return None


def run(snapshot: str = "pre_open") -> None:
    init_db()
    tickers = load_universe()
    print(f"[build] {len(tickers)} tickers, snapshot={snapshot}")

    # shared market data, fetched once
    nifty_closes = fetch_index_closes(config.NIFTY_TICKER)
    vix_value = fetch_latest_value(config.VIX_TICKER)

    dossiers: list[Dossier] = []
    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
        futures = {pool.submit(build_one, t, nifty_closes): t for t in tickers}
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

    print(f"[build] wrote {len(dossiers)} dossiers to {config.DOSSIER_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--close", action="store_true", help="post-close snapshot")
    args = ap.parse_args()
    run(snapshot="post_close" if args.close else "pre_open")
