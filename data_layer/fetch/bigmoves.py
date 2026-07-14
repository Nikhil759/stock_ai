"""
Big trades — bulk deals, block deals, and insider (PIT) disclosures from NSE.

Fetches market-wide snapshots once per run (cached in memory + per-day disk),
then filters per ticker. Empty arrays are normal when a stock had no activity.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import nse_client
from ..config import CACHE_DIR

_CACHE_DIR = CACHE_DIR / "bigmoves"
_lock = threading.Lock()
_MEM: dict[str, Any] = {}

_LARGEDEAL_URL = nse_client.NSE_BASE + "/api/snapshot-capital-market-largedeal"
_LARGEDEAL_REFERER = nse_client.NSE_BASE + "/report-detail/display-bulk-and-block-deals"
_PIT_URL = (
    nse_client.NSE_BASE
    + "/api/corporates-pit?index=equities&from_date={from_date}&to_date={to_date}"
)
_PIT_REFERER = nse_client.NSE_BASE + "/companies-listing/corporate-filings-insider-trading"


def _today() -> str:
    return date.today().isoformat()


def _disk_get(name: str) -> Any | None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{name}_{_today()}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _disk_put(name: str, data: Any) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{name}_{_today()}.json"
    try:
        path.write_text(json.dumps(data, default=str))
    except Exception as e:
        print(f"[FETCH] bigmoves cache write failed: {e}")


def _norm_side(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ("buy", "b", "purchase"):
        return "buy"
    if s in ("sell", "s", "sale"):
        return "sell"
    return s or ""


def _to_float(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(v: Any) -> int:
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _row_symbol(row: dict) -> str:
    for key in ("symbol", "Symbol", "SYMBOL", "secuirty", "security", "Security Name"):
        if key in row and row[key]:
            return str(row[key]).strip().upper()
    return ""


def _parse_bulk_row(row: dict) -> dict:
    return {
        "date": str(
            row.get("date")
            or row.get("Date")
            or row.get("BD_DT")
            or ""
        ),
        "quantity": _to_int(
            row.get("qty")
            or row.get("quantity")
            or row.get("Quantity")
            or row.get("qnty")
        ),
        "price": _to_float(
            row.get("watp")
            or row.get("price")
            or row.get("Price")
            or row.get("avgPrice")
        ),
        "side": _norm_side(
            row.get("buySell")
            or row.get("buy_sell")
            or row.get("clientType")
            or row.get("BD_BUY_SELL")
            or row.get("side")
        ),
    }


def _parse_block_row(row: dict) -> dict:
    return {
        "date": str(row.get("date") or row.get("Date") or row.get("BD_DT") or ""),
        "quantity": _to_int(
            row.get("qty") or row.get("quantity") or row.get("Quantity")
        ),
        "price": _to_float(
            row.get("watp") or row.get("price") or row.get("Price") or row.get("avgPrice")
        ),
    }


def _parse_insider_row(row: dict) -> dict:
    acq = str(
        row.get("acqMode")
        or row.get("acquisitionMode")
        or row.get("tknType")
        or row.get("type")
        or ""
    ).lower()
    side = "buy"
    if any(w in acq for w in ("sale", "sell", "dispos")):
        side = "sell"
    elif any(w in acq for w in ("buy", "purch", "acquis")):
        side = "buy"
    return {
        "date": str(
            row.get("anex")
            or row.get("date")
            or row.get("broadcastdate")
            or row.get("broadcastDate")
            or ""
        ),
        "type": side,
        "quantity": _to_int(
            row.get("secAcq")
            or row.get("secSell")
            or row.get("quantity")
            or row.get("noOfShare")
        ),
        "person_category": str(
            row.get("personCategory")
            or row.get("buySalPer")
            or row.get("remarks")
            or row.get("acquirerOrDisposer")
            or ""
        ),
    }


def _load_largedeals() -> dict[str, list]:
    """Market-wide bulk + block deals for the latest session NSE publishes."""
    with _lock:
        if "largedeals" in _MEM:
            return _MEM["largedeals"]
        cached = _disk_get("largedeals")
        if cached is not None:
            _MEM["largedeals"] = cached
            return cached

    data = nse_client.fetch_json(
        _LARGEDEAL_URL,
        referer=_LARGEDEAL_REFERER,
        label="largedeals",
    )
    parsed = {"bulk": [], "block": []}
    if isinstance(data, dict):
        bulk = data.get("BULK_DEALS_DATA") or data.get("bulk_deals") or []
        block = data.get("BLOCK_DEALS_DATA") or data.get("block_deals") or []
        if isinstance(bulk, list):
            parsed["bulk"] = bulk
        if isinstance(block, list):
            parsed["block"] = block
    elif data is None:
        print("[FETCH] bigmoves largedeals unavailable — empty arrays for all stocks")

    with _lock:
        _MEM["largedeals"] = parsed
        _disk_put("largedeals", parsed)
    return parsed


def _load_insider() -> list:
    """Market-wide PIT disclosures for the last ~7 calendar days."""
    with _lock:
        if "insider" in _MEM:
            return _MEM["insider"]
        cached = _disk_get("insider")
        if cached is not None:
            _MEM["insider"] = cached
            return cached

    to_d = date.today()
    from_d = to_d - timedelta(days=7)
    url = _PIT_URL.format(
        from_date=from_d.strftime("%d-%m-%Y"),
        to_date=to_d.strftime("%d-%m-%Y"),
    )
    data = nse_client.fetch_json(url, referer=_PIT_REFERER, label="insider-pit")
    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("dataList") or []
        if not isinstance(rows, list):
            rows = []

    with _lock:
        _MEM["insider"] = rows
        _disk_put("insider", rows)
    return rows


def prefetch_bigmoves() -> None:
    """Warm market-wide caches once before the per-stock build loop."""
    print("[FETCH] bigmoves prefetch start")
    _load_largedeals()
    _load_insider()
    print("[FETCH] bigmoves prefetch done")


def fetch_big_trades(ticker: str) -> dict:
    """Per-stock big_trades block. Always returns the schema shape (never None)."""
    sym = ticker.strip().upper()
    try:
        deals = _load_largedeals()
        insider_rows = _load_insider()

        bulk = [
            _parse_bulk_row(r)
            for r in deals.get("bulk", [])
            if isinstance(r, dict) and _row_symbol(r) == sym
        ]
        block = [
            _parse_block_row(r)
            for r in deals.get("block", [])
            if isinstance(r, dict) and _row_symbol(r) == sym
        ]
        insider = [
            _parse_insider_row(r)
            for r in insider_rows
            if isinstance(r, dict) and _row_symbol(r) == sym
        ]

        print(
            f"[FETCH] bigmoves {sym} OK "
            f"(bulk={len(bulk)} block={len(block)} insider={len(insider)})"
        )
        return {
            "bulk_deals": bulk,
            "block_deals": block,
            "insider_trades": insider,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        print(f"[FETCH] bigmoves {sym} FAILED: {e}")
        return {
            "bulk_deals": [],
            "block_deals": [],
            "insider_trades": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        }
