"""
Phase 0 - fundamentals fetch. Delegates to backend/data.py's proven, cached
yfinance fundamentals fetch and adapts its dict shape into the dossier's
Fundamentals dataclass. Graham Number is recomputed with the classic formula
(sqrt(22.5 * EPS * Book Value)) since that's what the schema field name
means - backend's own `graham` field (PE*PB) is a different shortcut used
for its own fair-value blend and is not reused here.
"""
from __future__ import annotations
import sys
from pathlib import Path

from ..dossier import Fundamentals

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import data as _backend_data


def _graham_number(eps, book_value):
    try:
        if eps and book_value and eps > 0 and book_value > 0:
            return round((22.5 * eps * book_value) ** 0.5, 2)
    except Exception:
        pass
    return None


def fetch_fundamentals(ticker: str) -> Fundamentals:
    f = Fundamentals()
    raw = _backend_data.fetch_stock_fundamentals(ticker)  # cached, proven
    if not raw:
        return f

    price, pe, pb = raw.get("price"), raw.get("pe"), raw.get("pb")

    f.price = price
    f.market_cap_cr = round(raw["marketCap"] / 1e7, 0) if raw.get("marketCap") else None
    f.pe = pe
    f.pb = pb
    f.debt_to_equity = raw.get("de")
    f.roe = raw.get("roe")
    f.current_ratio = raw.get("curr")
    f.fair_value_estimate = raw.get("fair")

    # EPS / book value aren't in backend's cached dict; derive them the same
    # way backend itself does internally for `fair`, then use for eps_ttm
    # and the real Graham Number. dividend_yield stays None (Phase 2).
    eps = (price / pe) if price and pe and pe > 0 else None
    book_value = (price / pb) if price and pb and pb > 0 else None
    f.eps_ttm = round(eps, 2) if eps is not None else None
    f.graham_number = _graham_number(eps, book_value)

    return f
