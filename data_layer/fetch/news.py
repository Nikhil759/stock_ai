"""
Phase 3 — news + sentiment (Marketaux).

Fills the strategy-neutral NewsBlock: recent news items gated by match_score,
capped at MAX_NEWS_ITEMS, plus an aggregate sentiment label and a short
plain-language comparison of that sentiment against the stock's own 6-month
move. No strategy logic lives here — just facts for the selector to read.

HONEST CAVEATS:
- Marketaux's free tier is 100 requests/day per key, max 3 articles/request.
  MAX_NEWS_ITEMS is a cap, not a guarantee — the plan's own `limit` ceiling
  governs what actually comes back.
- Two keys (MARKETAUX_API_KEY1 / MARKETAUX_API_KEY2, or a single
  MARKETAUX_API_KEY) are round-robined across tickers so the ~200-stock
  universe fits within combined daily quota. If a key exhausts (HTTP 402)
  mid-run, it's retired for the rest of the run and the remaining key(s)
  pick up the slack.
- Indian NSE tickers are queried as `{ticker}.NS`, matching how Marketaux
  tags Indian entities (confirmed live against marketaux.com's own India
  example, e.g. "GICRE.NS").
- Marketaux's news/all response has no explicit news-category/event-type
  field, so `event_type` stays "general" — best-effort, not invented.
- Everything degrades to an empty (but valid) NewsBlock on any failure,
  missing key, empty response, or exhausted quota — never raises.
- Results are cached per-day on disk (like Phase 2/3's other fetchers), keyed
  per ticker, so re-running the build multiple times in one day doesn't
  re-spend quota on tickers already checked today. Only successful responses
  are cached — a quota/network failure is retried on the next run, never
  cached as "no news".
"""
from __future__ import annotations
import itertools
import json
import os
import threading
import time
from datetime import date

from dotenv import load_dotenv

from ..dossier import NewsBlock
from ..config import ROOT, CACHE_DIR as _BASE_CACHE_DIR

load_dotenv(ROOT / ".env")  # .env ships with the code, not runtime state -> stays repo-relative

MATCH_SCORE_THRESHOLD = 40
MAX_NEWS_ITEMS = 8
REQUEST_DELAY = 0.25  # be gentle — the daily quota is small and shared

_NEWS_URL = "https://api.marketaux.com/v1/news/all"
_CACHE_DIR = _BASE_CACHE_DIR / "news"
_MEM: dict[str, dict] = {}   # in-run cache


# ---------- API key rotation (MARKETAUX_API_KEY1/2, falls back to MARKETAUX_API_KEY) ----------

def _load_keys() -> list[str]:
    keys = []
    for name in ("MARKETAUX_API_KEY1", "MARKETAUX_API_KEY2", "MARKETAUX_API_KEY"):
        v = os.getenv(name)
        if v:
            keys.append(v)
    return keys


_KEYS = _load_keys()
_key_lock = threading.Lock()
_key_cycle = itertools.cycle(_KEYS) if _KEYS else None
_exhausted: set[str] = set()


def _next_key() -> str | None:
    """Round-robin through live keys; skip any retired for quota exhaustion."""
    if not _KEYS:
        return None
    with _key_lock:
        live = [k for k in _KEYS if k not in _exhausted]
        if not live:
            return None
        for _ in range(len(_KEYS)):
            k = next(_key_cycle)
            if k not in _exhausted:
                return k
    return None


def _retire_key(key: str) -> None:
    with _key_lock:
        _exhausted.add(key)
        left = len(_KEYS) - len(_exhausted)
        print(f"[news] Marketaux key ...{key[-4:]} exhausted for this run, {left} key(s) left")


# ---------- fetch (cached per-day, per-ticker) ----------

def _fetch_raw(ticker: str) -> dict | None:
    """Call Marketaux for one ticker. Tries every live key once before giving
    up. Cached per-day on disk + in memory — only successful responses are
    cached, so a quota/network failure gets retried on the next run."""
    if ticker in _MEM:
        return _MEM[ticker]

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{ticker}_{date.today().isoformat()}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            _MEM[ticker] = data
            return data
        except Exception:
            pass

    if not _KEYS:
        print("[news] no MARKETAUX_API_KEY1/2 (or MARKETAUX_API_KEY) set — skipping")
        return None

    import requests
    symbol = f"{ticker}.NS"
    tried = 0
    while tried < len(_KEYS):
        key = _next_key()
        if key is None:
            return None
        tried += 1
        try:
            resp = requests.get(
                _NEWS_URL,
                params={
                    "api_token": key,
                    "symbols": symbol,
                    "filter_entities": "true",
                    "must_have_entities": "true",
                    "language": "en",
                    "min_match_score": MATCH_SCORE_THRESHOLD,
                    "limit": MAX_NEWS_ITEMS,
                    "sort": "published_at",
                },
                timeout=15,
            )
            if resp.status_code in (402, 429):
                _retire_key(key)
                continue
            resp.raise_for_status()
            if REQUEST_DELAY:
                time.sleep(REQUEST_DELAY)
            data = resp.json()
            cache_file.write_text(json.dumps(data))
            _MEM[ticker] = data
            return data
        except Exception as e:
            print(f"[news] {ticker}: {e}")
            return None
    return None


# ---------- parse ----------

def _short_summary(text: str | None, max_len: int = 220) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


def _sentiment_label(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def _sentiment_vs_price(label: str, return_6m: float | None) -> str:
    if return_6m is None:
        return f"sentiment is {label}; no 6-month price move available for comparison"
    if label == "positive" and return_6m < 0:
        return "sentiment positive while stock down over 6m — positive divergence"
    if label == "negative" and return_6m > 0:
        return "sentiment negative while stock up over 6m — negative divergence"
    if label == "neutral":
        return "sentiment neutral; no strong signal either way vs the 6-month move"
    return "sentiment and price broadly aligned"


def _entity_for_symbol(article: dict, symbol: str) -> dict | None:
    entities = article.get("entities") or []
    for ent in entities:
        if (ent.get("symbol") or "").upper() == symbol.upper():
            return ent
    return entities[0] if entities else None


def _parse(raw: dict, ticker: str) -> tuple[NewsBlock, str | None]:
    articles = (raw or {}).get("data") or []
    symbol = f"{ticker}.NS"

    items = []
    scores = []
    for art in articles:
        ent = _entity_for_symbol(art, symbol)
        if not ent:
            continue
        match_score = ent.get("match_score")
        if match_score is None or match_score < MATCH_SCORE_THRESHOLD:
            continue
        sentiment = ent.get("sentiment_score")
        items.append({
            "date": art.get("published_at"),
            "headline": art.get("title"),
            "summary": _short_summary(art.get("description") or art.get("snippet")),
            "sentiment_score": sentiment,
            "match_score": match_score,
            "event_type": "general",  # Marketaux exposes no category field here
            "source": art.get("source"),
        })
        if sentiment is not None:
            scores.append(sentiment)
        if len(items) >= MAX_NEWS_ITEMS:
            break

    block = NewsBlock(match_score_threshold=MATCH_SCORE_THRESHOLD, items=items)
    label = None
    if scores:
        label = _sentiment_label(sum(scores) / len(scores))
        block.aggregate_sentiment = label
    return block, label


# ---------- public entry point (build.py already calls this) ----------

def fetch_news(ticker: str, return_6m: float | None = None) -> NewsBlock:
    raw = _fetch_raw(ticker)
    if not raw:
        return NewsBlock(match_score_threshold=MATCH_SCORE_THRESHOLD)
    block, label = _parse(raw, ticker)
    if label is not None:
        block.sentiment_vs_price = _sentiment_vs_price(label, return_6m)
    return block


# ---------- exploration helper: dump raw response to confirm India coverage ----------

def explore_raw(ticker: str) -> None:
    raw = _fetch_raw(ticker)
    if not raw:
        print(f"No data returned for {ticker}. Check MARKETAUX_API_KEY1/2, network, or plan quota.")
        return
    print(f"=== RAW MARKETAUX RESPONSE for {ticker}.NS ===")
    print(json.dumps(raw, indent=2)[:4000])
    block = fetch_news(ticker, return_6m=None)
    print("=== PARSED NewsBlock ===")
    print(json.dumps(block.__dict__, indent=2, default=str))


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    explore_raw(sym)
