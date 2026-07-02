"""
Phase 0 - price/bar fetch. Delegates to the app's proven yfinance fetch
(backend/data.py: fetch_history, fetch_nifty_index_history) so the data
layer and the live app never drift on cleaning/caching behavior.
"""
from __future__ import annotations
import sys
from pathlib import Path

from ..config import HISTORY_PERIOD

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import data as _backend_data  # backend/data.py - proven fetch + cache + cleaning


def fetch_bars(ticker: str):
    """Daily OHLCV bars: cached per-day, cleaned, split/dividend-adjusted -
    identical to what the live app already fetches for this ticker."""
    return _backend_data.fetch_history(ticker, period=HISTORY_PERIOD)


def fetch_index_closes(index_ticker: str):
    """Nifty index closes. `index_ticker` kept for interface parity; the
    backend helper already has its own ^NSEI -> NIFTYBEES.NS fallback."""
    idx = _backend_data.fetch_nifty_index_history(period=HISTORY_PERIOD)
    return idx["closes"] if idx else None


def fetch_latest_value(ticker: str):
    """Latest close for a single ticker (e.g. India VIX). No backend
    equivalent exists for this, so it stays a direct, uncached yfinance call."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"[prices] latest {ticker}: {e}")
        return None
