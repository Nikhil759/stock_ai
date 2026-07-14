"""
Phase 3 — earnings dates + corporate actions (direct nseindia.com API calls).

Fills the Events block: next scheduled board meeting/earnings date (if NSE
has filed one yet), upcoming ex-dividend date, and a short list of recent
corporate actions (dividends, splits, bonus). Same style as Phase 2's
fundamentals_ext.py: unofficial NSE endpoints, a shared cookie-warmed
session, defensive nested-lookup parsing, per-day disk cache, degrade to an
empty Events block on any failure. No nsepython needed — plain `requests`
reproduces the same cookie-authenticated flow (verified live).

HONEST CAVEATS:
- NSE only files a board-meeting notice ~1-2 weeks before it happens, so
  `next_earnings_date` is often legitimately None — that's expected, not a
  bug. It only populates in the short window after NSE announces the next
  meeting.
- `recent_corporate_actions` and ex-dividend detection come from NSE's
  corporate-actions filing history, which lists both past and (if filed)
  future ex-dates.
- Everything degrades to an empty Events block on any failure.
"""
from __future__ import annotations
import json
from datetime import date, datetime
from urllib.parse import quote

from ..dossier import Events
from ..config import CACHE_DIR as _BASE_CACHE_DIR
from . import nse_client

_CACHE_DIR = _BASE_CACHE_DIR / "events"
_MEM: dict[str, list] = {}

_NSE_BASE = nse_client.NSE_BASE
_BOARD_MEETINGS_URL = _NSE_BASE + "/api/corporate-board-meetings?index=equities&symbol={symbol}"
_CORP_ACTIONS_URL = _NSE_BASE + "/api/corporates-corporateActions?index=equities&symbol={symbol}"
_EVENTS_REFERER = _NSE_BASE + "/companies-listing/corporate-filings-event-calendar"

_EARNINGS_KEYWORDS = ("financial result", "results", "audited")


# ---------- nested-lookup helper (defensive, same style as fundamentals_ext.py) ----------

def _safe(d, *paths):
    for path in paths:
        cur = d
        ok = True
        for key in path:
            try:
                cur = cur[key]
            except (KeyError, IndexError, TypeError):
                ok = False
                break
        if ok and cur is not None:
            return cur
    return None


def _parse_nse_date(s) -> date | None:
    """NSE dates look like '24-Apr-2026'."""
    if not s or not isinstance(s, str) or s.strip() in ("-", ""):
        return None
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


# ---------- fetch (cached per-day, per-endpoint) ----------

def _fetch_json(url: str, cache_key: str) -> list | None:
    if cache_key in _MEM:
        return _MEM[cache_key]

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{cache_key}_{date.today().isoformat()}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            _MEM[cache_key] = data
            return data
        except Exception:
            pass

    try:
        data = nse_client.fetch_json(
            url,
            referer=_EVENTS_REFERER,
            label=f"events:{cache_key}",
        )
        if data is not None:
            cache_file.write_text(json.dumps(data))
            _MEM[cache_key] = data
            return data
    except Exception as e:
        print(f"[FETCH] events {cache_key}: {e}")
    return None


def _fetch_board_meetings(symbol: str) -> list | None:
    url = _BOARD_MEETINGS_URL.format(symbol=quote(symbol, safe=""))
    return _fetch_json(url, f"boardmeetings_{symbol}")


def _fetch_corp_actions(symbol: str) -> list | None:
    url = _CORP_ACTIONS_URL.format(symbol=quote(symbol, safe=""))
    return _fetch_json(url, f"corpactions_{symbol}")


# ---------- parse (verified against a live response, 2026-07-03) ----------

def _next_earnings_date(meetings: list | None) -> date | None:
    if not meetings:
        return None
    today = date.today()
    forthcoming = []
    for m in meetings:
        d = _parse_nse_date(_safe(m, ("bm_date",)))
        if d is None or d < today:
            continue
        purpose = f"{_safe(m, ('bm_purpose',)) or ''} {_safe(m, ('bm_desc',)) or ''}".lower()
        forthcoming.append((d, purpose))
    if not forthcoming:
        return None
    forthcoming.sort(key=lambda t: t[0])
    for d, purpose in forthcoming:
        if any(kw in purpose for kw in _EARNINGS_KEYWORDS):
            return d
    return forthcoming[0][0]  # nearest forthcoming meeting even if purpose unclear


def _ex_dividend_and_actions(actions: list | None) -> tuple[date | None, list[str]]:
    if not actions:
        return None, []
    today = date.today()
    parsed = [
        (_parse_nse_date(_safe(a, ("exDate",))), _safe(a, ("subject",)) or "")
        for a in actions
    ]

    future_ex = sorted(ex for ex, _ in parsed if ex and ex >= today)
    ex_dividend_date = future_ex[0] if future_ex else None

    dated = sorted(((ex, subj) for ex, subj in parsed if ex is not None), reverse=True)
    recent = [f"{subj} (ex: {ex.isoformat()})" for ex, subj in dated[:5]]
    return ex_dividend_date, recent


# ---------- public entry point (build.py already calls this) ----------

def fetch_events(ticker: str) -> Events:
    try:
        meetings = _fetch_board_meetings(ticker)
        actions = _fetch_corp_actions(ticker)

        e = Events()
        next_earnings = _next_earnings_date(meetings)
        if next_earnings:
            e.next_earnings_date = next_earnings.isoformat()
            e.days_to_earnings = (next_earnings - date.today()).days

        ex_div, recent = _ex_dividend_and_actions(actions)
        if ex_div:
            e.ex_dividend_date = ex_div.isoformat()
        e.recent_corporate_actions = recent
        return e
    except Exception as e:
        print(f"[FETCH] events {ticker}: {e}")
        return Events()


# ---------- exploration helper: dump raw structure to confirm mapping ----------

def explore_raw(ticker: str) -> None:
    meetings = _fetch_board_meetings(ticker)
    actions = _fetch_corp_actions(ticker)
    print(f"=== RAW board meetings for {ticker} ({len(meetings) if meetings else 0}) ===")
    print(json.dumps((meetings or [])[:3], indent=2))
    print(f"=== RAW corporate actions for {ticker} ({len(actions) if actions else 0}) ===")
    print(json.dumps((actions or [])[:3], indent=2))
    print("=== PARSED Events ===")
    print(json.dumps(fetch_events(ticker).__dict__, indent=2, default=str))


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    explore_raw(sym)
