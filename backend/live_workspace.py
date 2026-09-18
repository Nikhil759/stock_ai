"""Who may use the Live execution workspace (real Zerodha orders)."""
from __future__ import annotations

import os

_DEFAULT_LIVE_EMAIL = "bn5799@gmail.com"


def live_workspace_email() -> str:
    return (os.getenv("LIVE_WORKSPACE_EMAIL") or _DEFAULT_LIVE_EMAIL).strip().lower()


def live_workspace_allowed(email: str | None) -> bool:
    """True only for the configured live-beta user."""
    if not email:
        return False
    return email.strip().lower() == live_workspace_email()


def effective_execution_workspace(stored: str | None, email: str | None) -> str:
    """Return paper when live is stored but the user is not on the allowlist."""
    mode = (stored or "paper").strip().lower()
    ws = mode if mode in ("paper", "live") else "paper"
    if ws == "live" and not live_workspace_allowed(email):
        return "paper"
    return ws
