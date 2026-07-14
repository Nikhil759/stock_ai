"""Postgres connection handling for Wolf Capital."""

from __future__ import annotations

import os
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


def get_database_url() -> str:
    url = os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_DATABASE_URL or DATABASE_URL must be set in the environment"
        )
    return url


@contextmanager
def get_connection() -> Iterator[PgConnection]:
    """Yield a psycopg2 connection; commit on success, rollback on error, always close."""
    conn = psycopg2.connect(get_database_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
