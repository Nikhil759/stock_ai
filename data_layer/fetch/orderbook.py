"""
Order book + circuit limits via Kite Connect quote (read-only).

No order-placement code. Uses a cached access token only — never prompts
for interactive login during a dossier build. If no token is available,
returns None so the dossier still builds with `"order_book": null`.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..config import ROOT

_DepthLevel = dict[str, float | int]


def _token_path() -> Path:
    return ROOT / f".kite_token_{date.today().isoformat()}"


def _load_access_token() -> str | None:
    env = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if env:
        return env
    path = _token_path()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    return None


def _get_kite_readonly():
    """Build a KiteConnect client from a cached/env token, or return None."""
    api_key = os.getenv("KITE_API_KEY", "").strip()
    if not api_key:
        print("[FETCH] orderbook SKIP — KITE_API_KEY not set")
        return None
    token = _load_access_token()
    if not token:
        print(
            "[FETCH] orderbook SKIP — no Kite access token "
            f"(set KITE_ACCESS_TOKEN or create {_token_path().name})"
        )
        return None
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        # backend ships kiteconnect; allow import from backend when on path
        backend = str(ROOT / "backend")
        if backend not in sys.path:
            sys.path.insert(0, backend)
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            print("[FETCH] orderbook SKIP — kiteconnect not installed")
            return None

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token)
    return kite


def _nse_key(ticker: str) -> str:
    t = ticker.strip().upper()
    return t if ":" in t else f"NSE:{t}"


def _normalize_depth_side(levels: list | None) -> list[_DepthLevel]:
    out: list[_DepthLevel] = []
    for level in (levels or [])[:5]:
        if not isinstance(level, dict):
            continue
        out.append(
            {
                "price": float(level.get("price") or 0),
                "quantity": int(level.get("quantity") or 0),
                "orders": int(level.get("orders") or 0),
            }
        )
    while len(out) < 5:
        out.append({"price": 0.0, "quantity": 0, "orders": 0})
    return out


def _quote_to_order_book(quote: dict) -> dict[str, Any]:
    depth = quote.get("depth") or {}
    return {
        "depth": {
            "buy": _normalize_depth_side(depth.get("buy")),
            "sell": _normalize_depth_side(depth.get("sell")),
        },
        "circuit_limit_upper": float(quote.get("upper_circuit_limit") or 0),
        "circuit_limit_lower": float(quote.get("lower_circuit_limit") or 0),
        "volume": int(quote.get("volume") or 0),
        "average_traded_price": float(quote.get("average_price") or 0),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_order_books(tickers: list[str]) -> dict[str, dict | None]:
    """Batch-fetch order books for many tickers. Missing/failed → None."""
    result: dict[str, dict | None] = {t.strip().upper(): None for t in tickers}
    if not tickers:
        return result

    kite = _get_kite_readonly()
    if kite is None:
        return result

    keys = [_nse_key(t) for t in tickers]
    key_to_ticker = {_nse_key(t): t.strip().upper() for t in tickers}

    # Kite allows large batches; chunk to stay safe.
    chunk_size = 200
    for i in range(0, len(keys), chunk_size):
        chunk = keys[i : i + chunk_size]
        try:
            print(f"[FETCH] kite quote batch size={len(chunk)} attempt=1/1")
            quotes = kite.quote(chunk) or {}
            print(f"[FETCH] kite quote batch OK ({len(quotes)} returned)")
        except Exception as e:
            print(f"[FETCH] kite quote batch FAILED: {e}")
            continue
        for key, quote in quotes.items():
            ticker = key_to_ticker.get(key)
            if not ticker or not isinstance(quote, dict):
                continue
            try:
                result[ticker] = _quote_to_order_book(quote)
            except Exception as e:
                print(f"[FETCH] orderbook {ticker} parse FAILED: {e}")
                result[ticker] = None

    ok = sum(1 for v in result.values() if v is not None)
    print(f"[FETCH] orderbook done — {ok}/{len(result)} stocks OK")
    return result


def fetch_order_book(ticker: str) -> dict | None:
    """Single-ticker convenience wrapper."""
    return fetch_order_books([ticker]).get(ticker.strip().upper())
