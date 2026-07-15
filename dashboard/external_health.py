"""
Live status checks for every external service Wolf Capital depends on.

Each check is best-effort and time-boxed: it never raises, always returns a
status dict, and callers run them concurrently (via asyncio.to_thread) so one
slow/unreachable service doesn't stall the whole health page.

Marketaux is deliberately NOT called live here (free tier is 100 req/day,
shared with the morning news fetch) — we only report whether keys are
configured, plus whatever the last cron run recorded.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parents[1]
load_dotenv(_REPO / ".env")


def _timed(fn):
    t0 = time.monotonic()
    try:
        detail = fn()
        ok = True
    except Exception as e:
        detail = str(e).strip() or type(e).__name__
        ok = False
    ms = round((time.monotonic() - t0) * 1000)
    return ok, detail, ms


def _check_kite() -> dict[str, Any]:
    """Zerodha Kite — reuses today's cached token if present; never logs in."""

    def run():
        import importlib.util
        import sys

        backend = _REPO / "backend"
        backend_str = str(backend)
        if backend_str not in sys.path:
            sys.path.insert(0, backend_str)

        path = backend / "fund_manager" / "kite_auth.py"
        spec = importlib.util.spec_from_file_location("_health_kite_auth", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load kite_auth.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if not (os.getenv("KITE_API_KEY") or "").strip():
            raise RuntimeError("KITE_API_KEY not set")

        kite = mod.get_kite_nonblocking()
        if kite is None:
            raise RuntimeError(
                "No verified session for today — run locally: "
                "`python -m scripts.refresh_kite_token --sync`"
            )
        profile = kite.profile()
        return f"Session OK — logged in as {profile.get('user_name') or profile.get('user_id') or 'Kite user'}"

    ok, detail, ms = _timed(run)
    result: dict[str, Any] = {
        "id": "kite",
        "label": "Zerodha Kite",
        "sub": "Auth + live LTP",
        "status": "ok" if ok else "warn",
        "detail": detail,
        "latency_ms": ms,
    }
    try:
        import sys

        repo_str = str(_REPO)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)
        from db.kite_token_store import load_token_metadata

        meta = load_token_metadata()
        if meta and meta.get("generated_at"):
            result["token_synced_at"] = meta["generated_at"]
    except Exception:
        pass
    return result


def _check_yfinance() -> dict[str, Any]:
    def run():
        import yfinance as yf

        t = yf.Ticker("RELIANCE.NS")
        fi = t.fast_info
        price = None
        for key in ("last_price", "lastPrice", "regular_market_price", "regularMarketPrice"):
            price = fi.get(key) if isinstance(fi, dict) else getattr(fi, key, None)
            if price:
                break
        if not price:
            raise RuntimeError("No price returned for RELIANCE.NS")
        return f"RELIANCE.NS last price ₹{float(price):,.2f}"

    ok, detail, ms = _timed(run)
    return {
        "id": "yfinance",
        "label": "yfinance",
        "sub": "Price fallback",
        "status": "ok" if ok else "down",
        "detail": detail,
        "latency_ms": ms,
    }


def _check_gemini() -> dict[str, Any]:
    def run():
        api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        from google import genai

        client = genai.Client(api_key=api_key)
        # Cheap metadata call (no generation) — just proves the key + network work.
        first = next(iter(client.models.list()), None)
        if first is None:
            raise RuntimeError("API key valid but no models visible")
        return "API key valid, models endpoint reachable"

    ok, detail, ms = _timed(run)
    return {
        "id": "gemini",
        "label": "Google Gemini",
        "sub": "Dossier scoring + Wolf Brain",
        "status": "ok" if ok else "down",
        "detail": detail,
        "latency_ms": ms,
    }


def _check_marketaux() -> dict[str, Any]:
    def run():
        keys = [
            (os.getenv(name) or "").strip()
            for name in ("MARKETAUX_API_KEY1", "MARKETAUX_API_KEY2", "MARKETAUX_API_KEY")
        ]
        n = len([k for k in keys if k])
        if n == 0:
            raise RuntimeError("No MARKETAUX_API_KEY1/2 (or MARKETAUX_API_KEY) configured")
        return f"{n} key(s) configured — live call skipped to preserve daily quota (100 req/day)"

    ok, detail, ms = _timed(run)
    return {
        "id": "marketaux",
        "label": "Marketaux",
        "sub": "News / sentiment",
        "status": "ok" if ok else "warn",
        "detail": detail,
        "latency_ms": ms,
    }


def _check_supabase() -> dict[str, Any]:
    def run():
        from db.connection import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return "Connected"

    ok, detail, ms = _timed(run)
    return {
        "id": "supabase",
        "label": "Supabase Postgres",
        "sub": "Wolves, trades, health runs",
        "status": "ok" if ok else "down",
        "detail": detail,
        "latency_ms": ms,
    }


CHECKS = (
    _check_kite,
    _check_yfinance,
    _check_gemini,
    _check_marketaux,
    _check_supabase,
)


def run_all_checks_sync() -> list[dict[str, Any]]:
    """Run every check concurrently (each is blocking I/O) and return in a
    stable order. Call via asyncio.to_thread from async routes — the whole
    batch is bounded by the slowest single check, not their sum."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        results = list(pool.map(lambda fn: fn(), CHECKS))
    return results
