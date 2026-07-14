"""
Market mood — daily FII/DII net buy/sell (market-wide, once per run).

Writes market_context/mood_{date}.json under STATE_DIR so all dossiers and
strategies share one figure instead of duplicating it 200 times.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import nse_client
from ..config import STATE_DIR

_FII_DII_URL = nse_client.NSE_BASE + "/api/fiidiiTradeReact"
_REFERER = nse_client.NSE_BASE + "/reports/fii-dii"

MOOD_DIR = STATE_DIR / "market_context"


def _to_float(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _pick_net(row: dict, *keys: str) -> float | None:
    for k in keys:
        if k in row and row[k] is not None and str(row[k]).strip() != "":
            return _to_float(row[k])
    return None


def _parse_fiidii(data: Any) -> tuple[float, float, str]:
    """Return (fii_net, dii_net, date_str) from NSE fiidiiTradeReact payload."""
    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("fiiDiiTrade") or []
        if not isinstance(rows, list):
            rows = []

    fii_net = 0.0
    dii_net = 0.0
    as_of = date.today().isoformat()

    for row in rows:
        if not isinstance(row, dict):
            continue
        category = str(
            row.get("category")
            or row.get("Category")
            or row.get("cat")
            or ""
        ).upper()
        net = _pick_net(
            row,
            "netValue",
            "net_value",
            "net",
            "value",
            "buyValue",  # fallback only if net missing — prefer net*
        )
        # Prefer explicit net fields; if only buy/sell present, derive.
        if net is None:
            buy = _pick_net(row, "buyValue", "buy_value", "buy")
            sell = _pick_net(row, "sellValue", "sell_value", "sell")
            if buy is not None and sell is not None:
                net = buy - sell
        if net is None:
            continue

        date_raw = (
            row.get("date")
            or row.get("Date")
            or row.get("tradedDate")
            or ""
        )
        if date_raw:
            as_of = str(date_raw)

        if "FII" in category or "FPI" in category:
            fii_net = net
        elif "DII" in category:
            dii_net = net

    # Normalise date if NSE used DD-MMM-YYYY
    try:
        if "-" in as_of and not as_of[:4].isdigit():
            as_of = datetime.strptime(as_of.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        as_of = date.today().isoformat()

    return fii_net, dii_net, as_of


def mood_path(as_of: str | None = None) -> Path:
    d = as_of or date.today().isoformat()
    return MOOD_DIR / f"mood_{d}.json"


def fetch_and_save_market_mood() -> dict | None:
    """Fetch once, write mood_{date}.json, return the payload (or None on failure)."""
    print("[FETCH] marketmood start")
    data = nse_client.fetch_json(
        _FII_DII_URL, referer=_REFERER, label="fiidii"
    )
    if data is None:
        print("[FETCH] marketmood FAILED — no file written")
        return None

    try:
        fii_net, dii_net, as_of = _parse_fiidii(data)
        payload = {
            "date": as_of,
            "fii_net": fii_net,
            "dii_net": dii_net,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        MOOD_DIR.mkdir(parents=True, exist_ok=True)
        path = mood_path(as_of)
        # Also write today's filename if NSE date differs, for easy lookup.
        path.write_text(json.dumps(payload, indent=2))
        today_path = mood_path(date.today().isoformat())
        if today_path != path:
            today_path.write_text(json.dumps(payload, indent=2))
        print(f"[FETCH] marketmood OK → {path}")
        return payload
    except Exception as e:
        print(f"[FETCH] marketmood FAILED: {e}")
        return None
