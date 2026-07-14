"""
Ensure a fresh Kite access token before order-book fetches.

Reuses backend/fund_manager/kite_auth.py (TOTP auto-login when configured).
Never prompts interactively during a dossier build — if TOTP isn't set up,
logs and continues so the rest of the pipeline still runs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ..config import ROOT


def _load_kite_auth():
    backend = ROOT / "backend"
    path = backend / "fund_manager" / "kite_auth.py"
    if not path.exists():
        return None
    backend_str = str(backend)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)
    # Load by file path so fund_manager.__init__ (heavy deps) is not imported.
    spec = importlib.util.spec_from_file_location("kite_auth_for_build", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ensure_kite_access_token() -> bool:
    """Refresh/validate today's Kite token. Returns True if a usable token exists."""
    print("[FETCH] kite token ensure start")
    try:
        kite_auth = _load_kite_auth()
        if kite_auth is None:
            print("[FETCH] kite token SKIP — backend kite_auth not found")
            return False

        if not kite_auth.totp_configured():
            # Still OK if a valid cached token already exists from manual login.
            cached = kite_auth._load_cached_token()
            if cached:
                kite = kite_auth._build_kite(cached)
                if kite_auth._verify_token(kite):
                    print(
                        f"[FETCH] kite token OK (cached {kite_auth._token_path().name})"
                    )
                    return True
            print(
                "[FETCH] kite token SKIP — TOTP not configured "
                "(set KITE_USER_ID / KITE_PASSWORD / KITE_TOTP_SECRET)"
            )
            return False

        kite_auth.refresh_access_token(force=False)
        print(f"[FETCH] kite token OK ({kite_auth._token_path().name})")
        return True
    except Exception as e:
        print(f"[FETCH] kite token FAILED: {e}")
        return False
