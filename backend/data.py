"""NSE universe and yfinance data helpers."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

NIFTY_50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC", "SBIN",
    "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "SUNPHARMA",
    "TITAN", "BAJFINANCE", "WIPRO", "ULTRACEMCO", "NESTLEIND", "HCLTECH", "POWERGRID",
    "NTPC", "TECHM", "M&M", "TATAMOTORS", "ADANIENT", "JSWSTEEL", "ONGC", "INDUSINDBK",
    "BAJAJFINSV", "COALINDIA", "TATASTEEL", "GRASIM", "HINDALCO", "DIVISLAB", "CIPLA",
    "DRREDDY", "EICHERMOT", "APOLLOHOSP", "HEROMOTOCO", "BRITANNIA", "BPCL", "TATACONSUM",
    "SBILIFE", "HDFCLIFE", "ADANIPORTS", "LTIM", "BEL", "PIDILITIND",
]

CACHE_DIR = Path(__file__).resolve().parent / "cache"
_INDEX_CACHE: dict | None = None


def to_ns(symbol: str) -> str:
    return f"{symbol}.NS"


def safe_float(value, default=None):
    if value is None:
        return default
    try:
        f = float(value)
        if f != f or not math.isfinite(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _clean_hist(data: dict) -> dict | None:
    """Normalize OHLCV — trim trailing invalid bars, use last valid close as price."""
    closes_raw = list(data.get("closes") or [])
    n = len(closes_raw)
    if n < 30:
        return None

    # Drop trailing bars with no valid close (yfinance incomplete "today" bar)
    end = n
    while end > 0:
        c = safe_float(closes_raw[end - 1])
        if c is not None and c > 0:
            break
        end -= 1
    if end < 30:
        return None

    def _slice(key: str) -> list[float | None]:
        raw = list(data.get(key) or [])
        if len(raw) != n:
            return [None] * end
        return [safe_float(x) for x in raw[:end]]

    closes = _slice("closes")
    opens = _slice("opens")
    highs = _slice("highs")
    lows = _slice("lows")

    clean_closes: list[float] = []
    clean_opens: list[float] = []
    clean_highs: list[float] = []
    clean_lows: list[float] = []
    clean_vols: list[float] = []

    vols_raw = list(data.get("volumes") or [])
    for i in range(end):
        c = closes[i]
        if c is None or c <= 0:
            return None
        o = opens[i] if opens[i] is not None and opens[i] > 0 else c
        h = highs[i] if highs[i] is not None and highs[i] > 0 else c
        l = lows[i] if lows[i] is not None and lows[i] > 0 else c
        v = safe_float(vols_raw[i], 0.0) if i < len(vols_raw) else 0.0
        clean_closes.append(c)
        clean_opens.append(o)
        clean_highs.append(h)
        clean_lows.append(l)
        clean_vols.append(v or 0.0)

    price = safe_float(data.get("price"))
    if price is None or price <= 0:
        price = clean_closes[-1]

    return {
        "ticker": data.get("ticker"),
        "closes": clean_closes,
        "opens": clean_opens,
        "highs": clean_highs,
        "lows": clean_lows,
        "volumes": clean_vols,
        "price": round(price, 2),
    }


def normalize_debt_to_equity(raw):
    v = safe_float(raw)
    if v is None:
        return None
    return v / 100 if v > 5 else v


def _cache_path(kind: str, symbol: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    day = date.today().isoformat()
    return CACHE_DIR / f"{kind}_{symbol}_{day}.json"


def _read_cache(path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _write_cache(path: Path, data: dict):
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def fetch_history(symbol: str, period: str = "1y") -> dict | None:
    """Daily OHLCV bars as lists."""
    import yfinance as yf

    cache = _read_cache(_cache_path("hist", symbol))
    if cache:
        cleaned = _clean_hist(cache)
        if cleaned:
            # Rewrite cache without trailing NaN bars
            if len(cleaned.get("closes") or []) != len(cache.get("closes") or []):
                _write_cache(_cache_path("hist", symbol), cleaned)
            return cleaned

    ticker = yf.Ticker(to_ns(symbol))
    df = ticker.history(period=period, auto_adjust=True)
    if df is None or df.empty or len(df) < 30:
        return None

    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        return None

    data = {
        "ticker": symbol,
        "closes": [round(float(x), 4) for x in df["Close"].tolist()],
        "opens": [round(float(x), 4) for x in df["Open"].tolist()],
        "highs": [round(float(x), 4) for x in df["High"].tolist()],
        "lows": [round(float(x), 4) for x in df["Low"].tolist()],
        "volumes": [float(x) if math.isfinite(float(x)) else 0.0 for x in df["Volume"].tolist()],
        "price": round(float(df["Close"].iloc[-1]), 2),
    }
    cleaned = _clean_hist(data)
    if not cleaned:
        return None
    _write_cache(_cache_path("hist", symbol), cleaned)
    return cleaned


def fetch_nifty_index_history(period: str = "1y") -> dict | None:
    global _INDEX_CACHE
    if _INDEX_CACHE:
        return _INDEX_CACHE
    import yfinance as yf

    for sym in ("^NSEI", "NIFTYBEES.NS"):
        df = yf.Ticker(sym).history(period=period, auto_adjust=True)
        if df is None or df.empty or len(df) < 50:
            continue
        df = df.dropna(subset=["Close"])
        if len(df) < 50:
            continue
        closes = [float(x) for x in df["Close"].tolist() if math.isfinite(float(x))]
        if len(closes) < 50:
            continue
        _INDEX_CACHE = {
            "closes": closes,
            "price": closes[-1],
        }
        return _INDEX_CACHE
    return None


def market_above_200dma() -> bool:
    idx = fetch_nifty_index_history()
    if not idx or len(idx["closes"]) < 200:
        return True
    from indicators import sma
    ma = sma(idx["closes"], 200)
    return ma is not None and idx["closes"][-1] > ma


def fetch_stock_fundamentals(symbol: str) -> dict | None:
    import yfinance as yf

    cache = _read_cache(_cache_path("fund", symbol))
    if cache:
        return cache

    ticker = yf.Ticker(to_ns(symbol))
    info = ticker.info or {}

    price = safe_float(info.get("currentPrice")) or safe_float(info.get("regularMarketPrice"))
    if not price or price <= 0:
        hist = ticker.history(period="5d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])

    if not price or price <= 0:
        return None

    pe = safe_float(info.get("trailingPE"))
    pb = safe_float(info.get("priceToBook"))
    roe = safe_float(info.get("returnOnEquity"))
    if roe is not None and abs(roe) <= 1:
        roe = roe * 100

    current_ratio = safe_float(info.get("currentRatio"))
    de = normalize_debt_to_equity(info.get("debtToEquity"))
    mcap = safe_float(info.get("marketCap"))

    graham = (pe * pb) if pe is not None and pb is not None else None

    fair = price
    if pe and pb and pe > 0 and pb > 0:
        eps = price / pe
        book = price / pb
        fair_pe = eps * 15
        fair_pb = book * 1.5
        fair = (fair_pe + fair_pb) / 2

    result = {
        "ticker": symbol,
        "name": info.get("shortName") or info.get("longName") or symbol,
        "sector": info.get("sector") or "—",
        "price": round(price, 2),
        "pe": round(pe, 2) if pe is not None else None,
        "pb": round(pb, 2) if pb is not None else None,
        "de": round(de, 2) if de is not None else None,
        "roe": round(roe, 1) if roe is not None else None,
        "curr": round(current_ratio, 2) if current_ratio is not None else None,
        "graham": round(graham, 1) if graham is not None else None,
        "fair": round(fair, 2),
        "marketCap": mcap,
    }
    _write_cache(_cache_path("fund", symbol), result)
    return result


def fetch_latest_price(symbol: str) -> float | None:
    """Fetch current/EOD quote from yfinance, bypassing fund cache."""
    import yfinance as yf

    ticker = yf.Ticker(to_ns(symbol))
    price = None

    try:
        fi = ticker.fast_info
        for key in ("last_price", "lastPrice", "regular_market_price", "regularMarketPrice"):
            if isinstance(fi, dict):
                price = safe_float(fi.get(key))
            else:
                price = safe_float(getattr(fi, key, None))
            if price and price > 0:
                break
    except Exception:
        price = None

    if not price or price <= 0:
        info = ticker.info or {}
        price = safe_float(info.get("currentPrice")) or safe_float(info.get("regularMarketPrice"))

    if not price or price <= 0:
        hist = ticker.history(period="5d", auto_adjust=True)
        if hist is not None and not hist.empty:
            hist = hist.dropna(subset=["Close"])
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])

    if not price or price <= 0:
        return None

    price = round(price, 2)

    fund_path = _cache_path("fund", symbol)
    cached = _read_cache(fund_path)
    if cached:
        cached["price"] = price
        _write_cache(fund_path, cached)

    return price


def fetch_stock_full(symbol: str) -> dict | None:
    """Fundamentals + 1y OHLCV merged."""
    fund = fetch_stock_fundamentals(symbol)
    hist = fetch_history(symbol)
    if not fund and not hist:
        return None
    out = {**(fund or {}), **(hist or {})}
    if fund and hist:
        out["price"] = hist["price"]
    out["ticker"] = symbol
    return out if out.get("price") else None
