"""Postgres connection handling for Wolf Capital."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import connection as PgConnection

# Load .env from repo root then cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_REPO_ROOT / ".env")
load_dotenv()

_CONNECT_TIMEOUT_SEC = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))


def _with_ssl(url: str) -> str:
    """Supabase requires SSL from cloud hosts (Railway, etc.)."""
    if "supabase.co" in url or "pooler.supabase.com" in url:
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
    return url


def get_database_url() -> str:
    """Resolve Postgres URL.

    On Railway, prefer SUPABASE_POOLER_URL when set — Supabase *direct* hosts
    (db.*.supabase.co) are IPv6-only and often fail from Railway containers.
    Use the Session pooler string from Supabase → Database → Connect.
    """
    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    if on_railway:
        url = (
            os.getenv("SUPABASE_POOLER_URL")
            or os.getenv("SUPABASE_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
    else:
        url = os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_DATABASE_URL or DATABASE_URL must be set in the environment"
        )
    return _with_ssl(url.strip())


def _redact_url(url: str) -> str:
    return re.sub(r":([^:@/]+)@", r":***@", url)


def connection_hint(exc: BaseException) -> str:
    """Human hint for common Supabase + Railway connection failures."""
    msg = str(exc).lower()
    if any(
        token in msg
        for token in (
            "could not connect",
            "timeout",
            "timed out",
            "network is unreachable",
            "connection refused",
            "no route to host",
            "server closed the connection",
            "ipv6",
        )
    ):
        return (
            " Supabase direct connections (db.*.supabase.co) often fail from Railway. "
            "In Supabase → Project Settings → Database, copy the **Session pooler** URI "
            "and set it as SUPABASE_DATABASE_URL (or SUPABASE_POOLER_URL) on stock_ai."
        )
    if "does not exist" in msg and "health_status" in msg:
        return " Run health_status/schema.sql in the Supabase SQL editor."
    return ""


@contextmanager
def get_connection() -> Iterator[PgConnection]:
    """Yield a psycopg2 connection; commit on success, rollback on error, always close."""
    url = get_database_url()
    try:
        conn = psycopg2.connect(url, connect_timeout=_CONNECT_TIMEOUT_SEC)
    except Exception as e:
        raise RuntimeError(
            f"Postgres connect failed ({type(e).__name__}: {e}). "
            f"URL={_redact_url(url)}.{connection_hint(e)}"
        ) from e
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
