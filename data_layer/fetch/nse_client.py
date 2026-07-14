"""
Shared NSE India HTTP client.

Every NSE-facing call goes through here so we consistently:
  1. Warm a browser-like session against the NSE homepage (cookies)
  2. Send realistic User-Agent / Accept-Language / Referer headers
  3. Rate-limit consecutive requests (min delay between calls)
  4. Retry with exponential backoff on failure / timeout
  5. Log every attempt with the [FETCH] prefix

One stock's failure never raises out of fetch_json — callers get None.
"""
from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

NSE_BASE = "https://www.nseindia.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# Tunables
MIN_DELAY_SEC = 1.5
MAX_ATTEMPTS = 3
BASE_BACKOFF_SEC = 2.0
REQUEST_TIMEOUT = 20

_lock = threading.Lock()
_session: requests.Session | None = None
_last_request_at = 0.0


def _label(url: str) -> str:
    path = urlparse(url).path or url
    return path.rstrip("/").split("/")[-1] or "nse"


def _ensure_session(referer: str | None = None) -> requests.Session:
    """Return a cookie-warmed session; re-warm if somehow missing."""
    global _session
    with _lock:
        if _session is None:
            s = requests.Session()
            s.headers.update(_DEFAULT_HEADERS)
            s.headers["Referer"] = referer or (NSE_BASE + "/")
            try:
                print("[FETCH] NSE session warm-up → GET /")
                r = s.get(NSE_BASE + "/", timeout=REQUEST_TIMEOUT)
                print(f"[FETCH] NSE session warm-up → status={r.status_code}")
            except Exception as e:
                print(f"[FETCH] NSE session warm-up FAILED: {e}")
            _session = s
        elif referer:
            _session.headers["Referer"] = referer
        return _session


def _rate_limit() -> None:
    global _last_request_at
    with _lock:
        now = time.monotonic()
        wait = MIN_DELAY_SEC - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def reset_session() -> None:
    """Drop the cached session (e.g. after repeated 401/403)."""
    global _session
    with _lock:
        _session = None


def fetch_json(
    url: str,
    *,
    referer: str | None = None,
    label: str | None = None,
) -> Any | None:
    """GET an NSE JSON endpoint with rate-limit + retries. Returns parsed JSON or None."""
    tag = label or _label(url)
    referer = referer or NSE_BASE + "/"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            _rate_limit()
            session = _ensure_session(referer=referer)
            print(f"[FETCH] NSE {tag} attempt={attempt}/{MAX_ATTEMPTS}")
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code in (401, 403):
                print(
                    f"[FETCH] NSE {tag} blocked status={resp.status_code} "
                    f"— resetting session"
                )
                reset_session()
                if attempt < MAX_ATTEMPTS:
                    time.sleep(BASE_BACKOFF_SEC * (2 ** (attempt - 1)))
                    continue
                print(f"[FETCH] NSE {tag} FAILED after retries (blocked)")
                return None
            resp.raise_for_status()
            data = resp.json()
            print(f"[FETCH] NSE {tag} OK")
            return data
        except Exception as e:
            print(f"[FETCH] NSE {tag} error attempt={attempt}: {e}")
            if attempt < MAX_ATTEMPTS:
                delay = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                print(f"[FETCH] NSE {tag} retrying in {delay:.1f}s")
                time.sleep(delay)
            else:
                print(f"[FETCH] NSE {tag} FAILED after retries")
                return None
    return None
