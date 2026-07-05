"""Thin Kite price reader — single entry point for live quotes."""

from __future__ import annotations

import logging

from fund_manager.kite_auth import get_kite

log = logging.getLogger(__name__)


def _to_nse_symbol(ticker: str) -> str:
    """Map bare ticker to Kite NSE instrument key."""
    t = ticker.strip().upper()
    if ":" in t:
        return t
    return f"NSE:{t}"


def _from_nse_symbol(key: str) -> str:
    if ":" in key:
        return key.split(":", 1)[1]
    return key


def get_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch last traded prices for NSE tickers via Kite LTP.

    Returns {ticker: price} for symbols that resolved. Missing or failed
    symbols are omitted (logged at debug).
    """
    if not tickers:
        return {}

    kite = get_kite()
    keys = [_to_nse_symbol(t) for t in tickers]
    key_to_ticker = {_to_nse_symbol(t): t.strip().upper() for t in tickers}

    try:
        quotes = kite.ltp(keys)
    except Exception as exc:
        log.warning("Kite LTP batch failed: %s", exc)
        return {}

    out: dict[str, float] = {}
    for key, data in (quotes or {}).items():
        ticker = key_to_ticker.get(key) or _from_nse_symbol(key)
        price = data.get("last_price") if isinstance(data, dict) else None
        if price is not None and price > 0:
            out[ticker] = round(float(price), 2)
        else:
            log.debug("No LTP for %s (%s)", ticker, key)

    for t in tickers:
        sym = t.strip().upper()
        if sym not in out:
            log.debug("Missing price for %s", sym)

    return out
