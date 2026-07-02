"""
Phase 2 — extended fundamentals from NSE (direct nseindia.com API call).

Fills the India-specific OWNERSHIP field from NSE's quarterly shareholding
pattern filings: promoter holding %. Growth/margin ratios, FII holding, and
promoter pledge are NOT reliably exposed by this endpoint (see caveats
below), so they stay None here (a screener.in source, or XBRL parsing,
could fill them later).

HONEST CAVEATS — read before trusting output:
- NSE endpoints are UNOFFICIAL and undocumented; nseindia.com requires a
  browser-like session (cookies + headers) before it answers API calls,
  which is what _get_session() below sets up. No third-party NSE wrapper
  library is needed for this — plain `requests` reproduces it.
- The live endpoint returns a LIST of quarterly filings (most recent first).
  Each record carries only the top-line SEBI Reg-31 aggregates: promoter %
  (`pr_and_prgrp`), public % (`public_val`), employee-trust %. Verified
  live against RELIANCE/TCS on 2026-07-03.
- 'fii_holding_pct' and 'promoter_pledge_pct' are NOT in this endpoint at
  all, for any symbol. That detail only exists inside the per-quarter XBRL
  filing (a ~500KB XML per company per quarter, linked via the filing's
  `xbrl` field) and needs real XBRL parsing to extract — out of scope here.
  They stay None. That's expected, not a bug.
- Everything degrades to None on any failure — the dossier stays valid.
- Results are cached per-day on disk (fundamentals move slowly, quarterly)
  to avoid hammering NSE even though this runs in the daily build.
"""
from __future__ import annotations
import json
import threading
from datetime import date
from urllib.parse import quote

from ..dossier import Fundamentals
from ..config import CACHE_DIR as _BASE_CACHE_DIR

_CACHE_DIR = _BASE_CACHE_DIR / "fundamentals"
_MEM: dict[str, list] = {}   # in-run cache

_NSE_BASE = "https://www.nseindia.com"
_SHAREHOLDING_URL = (
    _NSE_BASE + "/api/corporate-share-holdings-master?index=equities&symbol={symbol}"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _NSE_BASE + "/companies-listing/corporate-filings-shareholding-pattern",
}

_session_lock = threading.Lock()
_session = None


def _get_session():
    """Lazily create one cookie-warmed requests session, shared across the
    build's worker threads (NSE requires its anti-bot cookies to be set
    before the API will respond)."""
    global _session
    with _session_lock:
        if _session is None:
            import requests
            s = requests.Session()
            s.headers.update(_HEADERS)
            try:
                s.get(_NSE_BASE + "/", timeout=15)
            except Exception as e:
                print(f"[fundamentals_ext] NSE session warm-up failed: {e}")
            _session = s
        return _session


# ---------- nested-lookup helper (defensive) ----------

def _safe(d, *paths):
    """Try several candidate key-paths; return the first that resolves.
    Each path is a tuple of keys/indexes. Returns None if none match."""
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


def _to_float(v):
    if v is None:
        return None
    try:
        return round(float(str(v).replace("%", "").replace(",", "").strip()), 2)
    except (ValueError, TypeError):
        return None


# ---------- fetch ----------

def _fetch_raw(symbol: str) -> list | None:
    """Fetch NSE's quarterly shareholding filings list. Cached per-day on
    disk + in memory. Returns the raw list (most recent quarter first, per
    NSE) or None on any failure."""
    if symbol in _MEM:
        return _MEM[symbol]

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{symbol}_{date.today().isoformat()}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            _MEM[symbol] = data
            return data
        except Exception:
            pass

    try:
        session = _get_session()
        # quote() so tickers like "M&M" / "M&MFIN" don't split the query string
        url = _SHAREHOLDING_URL.format(symbol=quote(symbol, safe=""))
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data:
            cache_file.write_text(json.dumps(data))
            _MEM[symbol] = data
            return data
    except Exception as e:
        print(f"[fundamentals_ext] {symbol}: {e}")
    return None


# ---------- parse (verified against a live response, 2026-07-03) ----------

def _parse(raw: list) -> dict:
    """Map NSE's raw shareholding-master list into our fields. `raw` is a
    list of quarterly filings, most recent first; raw[0] is the latest.
    fii_holding_pct / promoter_pledge_pct stay None - not present in this
    endpoint at all (see module docstring), not a mapping miss."""
    if not raw:
        return {}
    # Candidate paths — several tried per field for resilience against NSE
    # renaming things; (0, "pr_and_prgrp") is the confirmed-live one.
    promoter = _safe(
        raw,
        (0, "pr_and_prgrp"),
        (0, "promoterAndPromoterGroup"),
        ("data", 0, "pr_and_prgrp"),
    )
    return {
        "promoter_holding_pct": _to_float(promoter),
        "promoter_pledge_pct": None,   # needs XBRL parsing — not implemented
        "fii_holding_pct": None,       # needs XBRL parsing — not implemented
    }


# ---------- public entry point (build.py already calls this) ----------

def enrich_fundamentals(ticker: str, f: Fundamentals) -> Fundamentals:
    raw = _fetch_raw(ticker)
    if not raw:
        return f  # unchanged; fields stay None
    parsed = _parse(raw)
    if parsed.get("promoter_holding_pct") is not None:
        f.promoter_holding_pct = parsed["promoter_holding_pct"]
    if parsed.get("promoter_pledge_pct") is not None:
        f.promoter_pledge_pct = parsed["promoter_pledge_pct"]
    if parsed.get("fii_holding_pct") is not None:
        f.fii_holding_pct = parsed["fii_holding_pct"]
    # revenue_growth_yoy, margins, fii_holding_change_qoq: not from NSE -> stay None
    return f


# ---------- exploration helper: dump raw structure to confirm mapping ----------

def explore_raw(symbol: str) -> None:
    raw = _fetch_raw(symbol)
    if not raw:
        print(f"No data returned for {symbol}. Check network / endpoint.")
        return
    print(f"=== RAW NSE RESPONSE for {symbol} ({len(raw)} filings) ===")
    print(json.dumps(raw[:2], indent=2)[:4000])
    print("=== PARSED WITH CURRENT MAPPING ===")
    print(json.dumps(_parse(raw), indent=2))


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    explore_raw(sym)
