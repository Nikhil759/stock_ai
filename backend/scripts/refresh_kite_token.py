#!/usr/bin/env python3
"""Refresh today's Kite access token (TOTP if configured, else interactive).

Usage (from backend/):
    python -m scripts.refresh_kite_token
    python -m scripts.refresh_kite_token --force

Cron example (08:00 IST weekdays ≈ 02:30 UTC):
    30 2 * * 1-5 cd /path/to/stock_ai/backend && ../.venv/bin/python -m scripts.refresh_kite_token
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_kite_auth():
    """Load kite_auth without importing fund_manager.__init__ (heavy deps)."""
    path = _BACKEND / "fund_manager" / "kite_auth.py"
    spec = importlib.util.spec_from_file_location("kite_auth", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Kite Connect access token")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Discard today's cached token and mint a new one",
    )
    args = parser.parse_args()

    kite_auth = _load_kite_auth()

    if not kite_auth.totp_configured():
        print(
            "TOTP not configured. Add to repo-root .env:\n"
            "  KITE_USER_ID=...\n"
            "  KITE_PASSWORD=...\n"
            "  KITE_TOTP_SECRET=...\n"
            "Falling back to interactive login.\n"
        )

    token = kite_auth.refresh_access_token(force=args.force)
    kite = kite_auth.get_kite()
    profile = kite.profile()
    print(
        f"Ready — {profile.get('user_name')} ({profile.get('user_id')}) "
        f"token={kite_auth._token_path().name} ({len(token)} chars)"
    )


if __name__ == "__main__":
    main()
