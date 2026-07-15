"""Live LTP overlay for deploy shortlists (no DB imports)."""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)


def today_ist() -> date:
    return datetime.now(IST).date()


def is_nse_market_open(*, now: datetime | None = None) -> bool:
    """True on NSE cash-session weekdays 09:15–15:30 IST."""
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return _MARKET_OPEN <= t <= _MARKET_CLOSE


def _fetch_shortlist_ltps(symbols: list[str]) -> dict[str, float]:
    from backend.wolf_api import _fetch_ltps

    return _fetch_ltps(symbols)


def apply_live_shortlist_prices(
    shortlist: list[dict],
    *,
    run_date: date | None = None,
    force: bool = False,
) -> list[dict]:
    """Replace frozen morning prices with live LTPs during today's market session."""
    day = run_date or today_ist()
    if not force and (day != today_ist() or not is_nse_market_open()):
        return shortlist

    symbols = [
        str(c.get("symbol", "")).strip().upper()
        for c in shortlist
        if c.get("symbol")
    ]
    if not symbols:
        return shortlist

    ltps = _fetch_shortlist_ltps(symbols)
    if not ltps:
        log.warning(
            "[DEPLOY] live price fetch empty — using frozen shortlist prices"
        )
        return shortlist

    updated = 0
    out: list[dict] = []
    for cand in shortlist:
        row = dict(cand)
        sym = str(row.get("symbol", "")).strip().upper()
        ltp = ltps.get(sym)
        if ltp and float(ltp) > 0:
            morning = row.get("price")
            row["price_morning"] = morning
            row["price"] = round(float(ltp), 2)
            updated += 1
            if morning is not None:
                log.info(
                    "[DEPLOY] %s live ₹%.2f (morning ₹%.2f)",
                    sym,
                    row["price"],
                    morning,
                )
            else:
                log.info("[DEPLOY] %s live ₹%.2f", sym, row["price"])
        out.append(row)

    log.info(
        "[DEPLOY] overlaid live LTP on %d/%d shortlist symbol(s)",
        updated,
        len(shortlist),
    )
    return out
