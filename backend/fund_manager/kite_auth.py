"""Kite Connect auth — manual login with daily token cache.

Callers use get_kite() only. A future TOTP auto-login can plug in behind
_authenticate() without changing the public API.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from repo_paths import find_repo_root

_ROOT = find_repo_root()
load_dotenv(_ROOT / ".env")

_kite: KiteConnect | None = None


def _token_path() -> Path:
    today = date.today().isoformat()
    return _ROOT / f".kite_token_{today}"


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


def login_url() -> str:
    """Browser login URL — user completes login and copies request_token."""
    return KiteConnect(api_key=_api_key()).login_url()


def _load_cached_token() -> str | None:
    path = _token_path()
    if not path.exists():
        return None
    token = path.read_text(encoding="utf-8").strip()
    return token or None


def _save_token(access_token: str) -> None:
    _token_path().write_text(access_token, encoding="utf-8")


def _exchange_request_token(request_token: str) -> str:
    kite = KiteConnect(api_key=_api_key())
    session = kite.generate_session(request_token, api_secret=_api_secret())
    return session["access_token"]


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
    """Placeholder for future TOTP auto-login — not implemented yet."""
    raise NotImplementedError("TOTP auto-login not configured")


def _authenticate() -> str:
    """Resolve an access token: cache hit, else interactive (TOTP later)."""
    cached = _load_cached_token()
    if cached:
        return cached
    # Future: if os.getenv("KITE_TOTP_SECRET"): return _authenticate_totp()
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

    Reuses today's cached token. Prompts for browser login if missing or
    invalid. Set force_login=True to discard cache and re-authenticate.
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
        access_token = _authenticate_interactive()
        kite = _build_kite(access_token)
        if not _verify_token(kite):
            raise RuntimeError("Kite authentication failed after re-login")

    _kite = kite
    return kite
