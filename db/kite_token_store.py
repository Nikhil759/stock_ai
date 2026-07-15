"""Persist daily Kite access tokens in Supabase for Railway stock_ai."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, time
from uuid import UUID
from zoneinfo import ZoneInfo

from db.connection import get_connection
from db.repository import ensure_user_from_auth_email

IST = ZoneInfo("Asia/Kolkata")


def _expires_at(generated_at: datetime | None = None) -> datetime:
    """Zerodha tokens invalidate at ~6:00 AM IST the day after issuance."""
    now = (generated_at or datetime.now(tz=IST)).astimezone(IST)
    expiry_date = now.date() + timedelta(days=1)
    return datetime.combine(expiry_date, time(6, 0), tzinfo=IST)


def resolve_owner_user_id() -> UUID:
    explicit = (os.getenv("KITE_TOKEN_USER_ID") or "").strip()
    if explicit:
        return UUID(explicit)
    email = (os.getenv("AUTHORIZED_EMAIL") or "").strip()
    if not email:
        raise RuntimeError(
            "Set AUTHORIZED_EMAIL or KITE_TOKEN_USER_ID for Kite token sync"
        )
    row = ensure_user_from_auth_email(email)
    if not row:
        raise RuntimeError(f"No users row for AUTHORIZED_EMAIL={email!r}")
    return UUID(str(row["id"]))


def load_valid_token() -> str | None:
    """Return today's unexpired token from Supabase, or None."""
    try:
        user_id = resolve_owner_user_id()
    except Exception:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT access_token
                FROM kite_auth_tokens
                WHERE user_id = %s AND expires_at > now()
                """,
                (str(user_id),),
            )
            row = cur.fetchone()
            if row:
                token = str(row[0]).strip()
                return token or None
    return None


def save_token(access_token: str) -> None:
    """Upsert today's access token for the ops user."""
    token = (access_token or "").strip()
    if not token:
        raise RuntimeError("access_token is empty")
    user_id = resolve_owner_user_id()
    generated_at = datetime.now(tz=IST)
    expires = _expires_at(generated_at)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kite_auth_tokens (user_id, access_token, generated_at, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    generated_at = EXCLUDED.generated_at,
                    expires_at = EXCLUDED.expires_at
                """,
                (str(user_id), token, generated_at, expires),
            )
        conn.commit()
