"""Kite Connect auth — daily token cache with optional TOTP auto-login.

Callers use get_kite() only.

Auth order when a fresh token is needed:
  1. If KITE_USER_ID + KITE_PASSWORD + KITE_TOTP_SECRET are set → TOTP auto-login
  2. Else → interactive browser login (paste request_token)

Access tokens expire ~6 AM IST each day. On Railway, TOTP login is blocked —
refresh locally and sync to Supabase:

    python -m scripts.refresh_kite_token --sync

Schedule on your Mac (~6:05 AM IST weekdays); see backend/README.md.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv
from kiteconnect import KiteConnect

from data_paths import get_data_dir
from repo_paths import find_repo_root

_ROOT = find_repo_root()
load_dotenv(_ROOT / ".env")

_kite: KiteConnect | None = None

_LOGIN_URL = "https://kite.zerodha.com/api/login"
_TWOFA_URL = "https://kite.zerodha.com/api/twofa"


def _token_path() -> Path:
    today = date.today().isoformat()
    return get_data_dir() / f".kite_token_{today}"


def _load_token_from_supabase() -> str | None:
    """Best-effort read from kite_auth_tokens — never raises."""
    try:
        import sys

        root = find_repo_root()
        root_str = str(root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        from db.kite_token_store import load_valid_token

        return load_valid_token()
    except Exception:
        return None


def _api_key() -> str:
    key = os.getenv("KITE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("KITE_API_KEY not set in .env")
    return key


def _api_secret() -> str:
    secret = os.getenv("KITE_API_SECRET", "").strip()
    if not secret:
        raise RuntimeError("KITE_API_SECRET not set in .env")
    return secret


def _totp_credentials() -> tuple[str, str, str] | None:
    """Return (user_id, password, totp_secret) if all three are configured."""
    user_id = os.getenv("KITE_USER_ID", "").strip()
    password = os.getenv("KITE_PASSWORD", "").strip()
    totp_secret = os.getenv("KITE_TOTP_SECRET", "").strip().replace(" ", "")
    if user_id and password and totp_secret:
        return user_id, password, totp_secret
    return None


def totp_configured() -> bool:
    return _totp_credentials() is not None


def login_url() -> str:
    """Browser login URL — user completes login and copies request_token."""
    return KiteConnect(api_key=_api_key()).login_url()


def _load_cached_token() -> str | None:
    path = _token_path()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token

    token = _load_token_from_supabase()
    if token:
        return token

    env = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    return env or None


def _save_token(access_token: str) -> None:
    _token_path().write_text(access_token, encoding="utf-8")


def _exchange_request_token(request_token: str) -> str:
    kite = KiteConnect(api_key=_api_key())
    session = kite.generate_session(request_token, api_secret=_api_secret())
    return session["access_token"]


def _extract_request_token(url: str) -> str | None:
    """Pull request_token from a redirect URL or requests exception message."""
    if not url:
        return None
    # Prefer regex — works on bare URLs and on ConnectionError strings like
    # "... url: /?request_token=Ab12... (Caused by ...)" where parse_qs would
    # capture trailing junk and break the token exchange.
    m = re.search(r"request_token=([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
    tokens = qs.get("request_token") or []
    return tokens[0] if tokens else None


def _authenticate_interactive() -> str:
    """Manual browser login — paste request_token from redirect URL."""
    url = login_url()
    print("\n--- Kite login required ---")
    print(f"1. Open this URL in your browser:\n   {url}\n")
    print("2. After login, copy the request_token from the redirect URL.")
    request_token = input("request_token: ").strip()
    if not request_token:
        raise RuntimeError("No request_token provided")
    access_token = _exchange_request_token(request_token)
    _save_token(access_token)
    print(f"Access token cached for today ({_token_path().name})\n")
    return access_token


def _authenticate_totp() -> str:
    """Headless login via Zerodha password + TOTP → request_token → access_token.

    Uses undocumented kite.zerodha.com login/twofa endpoints (community-standard
    for daily algo auth). Requires External TOTP enabled on the Zerodha account.
    """
    creds = _totp_credentials()
    if creds is None:
        raise RuntimeError(
            "TOTP auto-login needs KITE_USER_ID, KITE_PASSWORD, and KITE_TOTP_SECRET in .env"
        )
    user_id, password, totp_secret = creds

    try:
        import pyotp
    except ImportError as e:
        raise RuntimeError(
            "pyotp is required for TOTP auto-login — pip install pyotp"
        ) from e

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-Kite-Version": "3",
        }
    )

    print("[kite] TOTP auto-login: step 1/3 password…")
    login_resp = session.post(
        _LOGIN_URL,
        data={"user_id": user_id, "password": password},
        timeout=30,
    )
    login_resp.raise_for_status()
    login_body = login_resp.json()
    if login_body.get("status") != "success":
        raise RuntimeError(f"Kite password login failed: {login_body.get('message')}")
    request_id = login_body["data"]["request_id"]

    otp = pyotp.TOTP(totp_secret).now()
    print("[kite] TOTP auto-login: step 2/3 twofa…")
    twofa_resp = session.post(
        _TWOFA_URL,
        data={
            "user_id": user_id,
            "request_id": request_id,
            "twofa_value": otp,
            "twofa_type": "totp",
            "skip_session": "true",
        },
        timeout=30,
    )
    twofa_resp.raise_for_status()
    twofa_body = twofa_resp.json()
    if twofa_body.get("status") != "success":
        raise RuntimeError(f"Kite TOTP twofa failed: {twofa_body.get('message')}")

    print("[kite] TOTP auto-login: step 3/3 connect redirect…")
    connect_url = login_url()
    try:
        # Redirect to localhost often raises ConnectionError; token is in the URL.
        redirect = session.get(connect_url, allow_redirects=True, timeout=30)
        final_url = redirect.url
    except requests.exceptions.RequestException as e:
        final_url = ""
        # requests may expose the failed redirect URL on the error response
        resp = getattr(e, "response", None)
        if resp is not None and resp.url:
            final_url = resp.url
        err = str(e)
        if "request_token=" in err:
            final_url = err
        if not final_url and hasattr(e, "request") and e.request is not None:
            # Walk redirect history if present on a partial response
            pass

    request_token = _extract_request_token(final_url) if final_url else None

    if request_token is None:
        # Some environments leave the token only in redirect history
        try:
            redirect = session.get(connect_url, allow_redirects=False, timeout=30)
            location = redirect.headers.get("Location", "")
            request_token = _extract_request_token(location)
            # Follow one more hop if still on kite.zerodha.com/connect/...
            hops = 0
            while request_token is None and location and hops < 5:
                hops += 1
                nxt = session.get(location, allow_redirects=False, timeout=30)
                location = nxt.headers.get("Location", "")
                request_token = _extract_request_token(location) or _extract_request_token(
                    nxt.url
                )
        except requests.exceptions.RequestException as e:
            m = re.search(r"request_token=([A-Za-z0-9]+)", str(e))
            if m:
                request_token = m.group(1)

    if not request_token:
        raise RuntimeError(
            "TOTP login succeeded but could not extract request_token from redirect. "
            "Check Kite app redirect URL (e.g. http://127.0.0.1)."
        )

    access_token = _exchange_request_token(request_token)
    _save_token(access_token)
    print(f"[kite] TOTP auto-login OK — cached {_token_path().name}")
    return access_token


def refresh_access_token(*, force: bool = False) -> str:
    """Ensure today's access token exists. Prefer TOTP when configured.

    Returns the access token string. Safe to call from cron.
    """
    global _kite
    if not force:
        cached = _load_cached_token()
        if cached:
            kite = _build_kite(cached)
            if _verify_token(kite):
                _kite = kite
                return cached
            print("[kite] cached token invalid — refreshing")

    if _token_path().exists() and force:
        _token_path().unlink()

    if totp_configured():
        token = _authenticate_totp()
    else:
        token = _authenticate_interactive()

    kite = _build_kite(token)
    if not _verify_token(kite):
        raise RuntimeError("Kite token refresh produced an invalid session")
    _kite = kite
    return token


def _authenticate() -> str:
    """Resolve an access token: cache hit, else TOTP or interactive."""
    cached = _load_cached_token()
    if cached:
        return cached
    if totp_configured():
        return _authenticate_totp()
    return _authenticate_interactive()


def _build_kite(access_token: str) -> KiteConnect:
    kite = KiteConnect(api_key=_api_key())
    kite.set_access_token(access_token)
    return kite


def _verify_token(kite: KiteConnect) -> bool:
    try:
        kite.profile()
        return True
    except Exception:
        return False


def get_kite(*, force_login: bool = False) -> KiteConnect:
    """Return an authenticated KiteConnect instance.

    Reuses today's cached token. If missing/invalid: TOTP auto-login when
    configured, otherwise interactive browser login.
    """
    global _kite
    if _kite is not None and not force_login:
        return _kite

    if force_login and _token_path().exists():
        _token_path().unlink()

    access_token = _authenticate()
    kite = _build_kite(access_token)

    if not _verify_token(kite):
        if _token_path().exists():
            _token_path().unlink()
        # Prefer TOTP retry if configured; else fall back to interactive
        if totp_configured():
            access_token = _authenticate_totp()
        else:
            access_token = _authenticate_interactive()
        kite = _build_kite(access_token)
        if not _verify_token(kite):
            raise RuntimeError("Kite authentication failed after re-login")

    _kite = kite
    return kite


def get_kite_nonblocking() -> KiteConnect | None:
    """Return a Kite session ONLY if one is already usable — never authenticates.

    Safe to call from a live web request (e.g. a UI "refresh prices" click):
    it reuses the in-process session or today's cached token and verifies it
    with a cheap `profile()` call, but never falls through to TOTP or
    interactive login (which can block on `input()` or hit Zerodha's login
    endpoints on every request). Returns None if there's no valid session —
    callers should fall back to another price source (e.g. yfinance).
    """
    global _kite
    if _kite is not None:
        return _kite

    cached = _load_cached_token()
    if not cached:
        return None

    kite = _build_kite(cached)
    if not _verify_token(kite):
        return None

    _kite = kite
    return kite


def _to_nse_symbol(ticker: str) -> str:
    t = ticker.strip().upper()
    return t if ":" in t else f"NSE:{t}"


def _from_nse_symbol(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def get_ltp_nonblocking(tickers: list[str]) -> dict[str, float]:
    """Best-effort live LTPs from Zerodha for `tickers` — never blocks/raises.

    Uses `get_kite_nonblocking()`, so it only returns data when a verified
    session already exists for today; otherwise returns {} so the caller can
    fall back to another price source. Safe to call from a live web request.
    """
    if not tickers:
        return {}
    kite = get_kite_nonblocking()
    if kite is None:
        return {}

    keys = [_to_nse_symbol(t) for t in tickers]
    key_to_ticker = {_to_nse_symbol(t): t.strip().upper() for t in tickers}
    try:
        quotes = kite.ltp(keys)
    except Exception:
        return {}

    out: dict[str, float] = {}
    for key, data in (quotes or {}).items():
        ticker = key_to_ticker.get(key) or _from_nse_symbol(key)
        price = data.get("last_price") if isinstance(data, dict) else None
        if price is not None and price > 0:
            out[ticker] = round(float(price), 2)
    return out
