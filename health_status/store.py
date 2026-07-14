"""
Phase E — structured health status store.

Note: package is `health_status` (not `logging/`) to avoid shadowing Python's
stdlib `logging` module. Spec file path in the handover was adjusted for that.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parents[1]
load_dotenv(_REPO / ".env")

_supabase = None
_client_failed = False


def _supabase_url() -> str | None:
    return (
        os.getenv("SUPABASE_URL")
        or os.getenv("SUPABASE_PROJECT_URL")
        or ""
    ).strip() or None


def _supabase_key() -> str | None:
    return (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    ).strip() or None


def _get_supabase():
    """Lazy supabase-py client, or None.

    Cron/ops writes prefer the Postgres URL (bypasses RLS). Set
    HEALTH_STATUS_USE_SUPABASE_REST=1 to force REST upserts (needs service role
    or permissive RLS).
    """
    global _supabase, _client_failed
    if os.getenv("HEALTH_STATUS_USE_SUPABASE_REST", "").strip() not in ("1", "true", "yes"):
        return None
    if _client_failed:
        return None
    if _supabase is not None:
        return _supabase
    url, key = _supabase_url(), _supabase_key()
    if not url or not key:
        _client_failed = True
        return None
    try:
        from supabase import create_client

        _supabase = create_client(url, key)
        return _supabase
    except Exception as e:
        print(f"[HEALTH STATUS] supabase-py unavailable ({e}); using psycopg2 fallback")
        _client_failed = True
        return None


def _deep_merge(base: dict, patch: dict) -> dict:
    out = deepcopy(base) if base else {}
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def _compute_overall(stages: dict) -> str:
    if not stages:
        return "failed"

    shared_keys = ("fetch", "technicals", "market_context")
    shared_statuses = [
        (stages.get(k) or {}).get("status")
        for k in shared_keys
        if k in stages
    ]
    if any(s == "failed" for s in shared_statuses):
        return "failed"

    strat_fail = False
    strat_ok = False
    for group in ("funnels", "batch_scoring"):
        block = stages.get(group) or {}
        if not isinstance(block, dict):
            continue
        for _name, info in block.items():
            if not isinstance(info, dict):
                continue
            st = info.get("status")
            if st == "failed":
                strat_fail = True
            elif st == "success":
                strat_ok = True

    if strat_fail and strat_ok:
        return "partial"
    if strat_fail:
        return "partial"
    if stages.get("batch_scoring") or stages.get("funnels"):
        return "success"
    if shared_statuses and all(s in ("success", "skipped") for s in shared_statuses):
        return "success"
    return "partial"


def _row_from_db(row: dict) -> dict:
    stages = row.get("stages") or {}
    if isinstance(stages, str):
        stages = json.loads(stages)
    d = row.get("date")
    if hasattr(d, "isoformat"):
        d = d.isoformat()
    started = row.get("started_at")
    if hasattr(started, "isoformat"):
        started = started.isoformat()
    return {
        "date": d,
        "started_at": started,
        "stages": stages,
        "overall_status": row.get("overall_status"),
    }


def get_status(day: date | str | None = None) -> dict | None:
    if day is None:
        day = date.today()
    day_s = day.isoformat() if isinstance(day, date) else str(day)

    client = _get_supabase()
    if client is not None:
        try:
            res = (
                client.table("health_status")
                .select("*")
                .eq("date", day_s)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return _row_from_db(rows[0]) if rows else None
        except Exception as e:
            print(f"[HEALTH STATUS] supabase get_status failed: {e}")

    return _pg_get_status(day_s)


def get_recent_statuses(n: int = 5) -> list[dict]:
    client = _get_supabase()
    if client is not None:
        try:
            res = (
                client.table("health_status")
                .select("*")
                .order("date", desc=True)
                .limit(n)
                .execute()
            )
            return [_row_from_db(r) for r in (res.data or [])]
        except Exception as e:
            print(f"[HEALTH STATUS] supabase get_recent_statuses failed: {e}")

    return _pg_get_recent(n)


def start_run(day: date | None = None) -> dict:
    day = day or date.today()
    existing = get_status(day) or {}
    stages = existing.get("stages") or {}
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "date": day.isoformat(),
        "started_at": now,
        "stages": stages,
        "overall_status": existing.get("overall_status") or "partial",
    }
    return _upsert_row(row, log_label="start_run")


def update_stage(path: str, payload: dict, day: date | None = None) -> dict:
    """
    Merge a stage update into today's stages JSON and upsert.

    path examples: "fetch", "funnels.value", "batch_scoring.dip", "cache_saved"
    """
    day = day or date.today()
    existing = get_status(day) or {}
    stages = deepcopy(existing.get("stages") or {})

    parts = path.split(".")
    if len(parts) == 1:
        key = parts[0]
        prev = stages.get(key)
        if isinstance(prev, dict) and isinstance(payload, dict):
            stages[key] = _deep_merge(prev, payload)
        else:
            stages[key] = payload
    elif len(parts) == 2:
        parent, child = parts
        block = stages.get(parent) if isinstance(stages.get(parent), dict) else {}
        prev = block.get(child)
        if isinstance(prev, dict) and isinstance(payload, dict):
            block[child] = _deep_merge(prev, payload)
        else:
            block[child] = payload
        stages[parent] = block
    else:
        raise ValueError(f"unsupported stage path: {path}")

    row = {
        "date": day.isoformat(),
        "started_at": existing.get("started_at")
        or datetime.now(timezone.utc).isoformat(),
        "stages": stages,
        "overall_status": _compute_overall(stages),
    }
    return _upsert_row(row, log_label=f"stage:{path}")


def finalize(overall: str | None = None, day: date | None = None) -> dict:
    day = day or date.today()
    existing = get_status(day) or {}
    stages = existing.get("stages") or {}
    row = {
        "date": day.isoformat(),
        "started_at": existing.get("started_at")
        or datetime.now(timezone.utc).isoformat(),
        "stages": stages,
        "overall_status": overall or _compute_overall(stages),
    }
    return _upsert_row(row, log_label=f"finalize:{row['overall_status']}")


def _upsert_row(row: dict, *, log_label: str) -> dict:
    client = _get_supabase()
    if client is not None:
        try:
            res = (
                client.table("health_status")
                .upsert(row, on_conflict="date")
                .execute()
            )
            data = (res.data or [row])[0]
            print(
                f"[HEALTH STATUS] upsert {log_label} date={row['date']} "
                f"overall={row.get('overall_status')}"
            )
            return _row_from_db(data if isinstance(data, dict) else row)
        except Exception as e:
            print(f"[HEALTH STATUS] supabase upsert failed ({e}); trying psycopg2")

    saved = _pg_upsert(row)
    print(
        f"[HEALTH STATUS] upsert {log_label} date={row['date']} "
        f"overall={row.get('overall_status')} (psycopg2)"
    )
    return saved


def _pg_upsert(row: dict) -> dict:
    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO health_status (date, started_at, stages, overall_status)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (date) DO UPDATE SET
                    started_at = COALESCE(EXCLUDED.started_at, health_status.started_at),
                    stages = EXCLUDED.stages,
                    overall_status = EXCLUDED.overall_status
                RETURNING date, started_at, stages, overall_status
                """,
                (
                    row["date"],
                    row.get("started_at"),
                    json.dumps(row.get("stages") or {}),
                    row.get("overall_status"),
                ),
            )
            r = cur.fetchone()
            cols = [d[0] for d in cur.description]
            return _row_from_db(dict(zip(cols, r)))


def _pg_get_status(day_s: str) -> dict | None:
    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT date, started_at, stages, overall_status "
                "FROM health_status WHERE date = %s",
                (day_s,),
            )
            r = cur.fetchone()
            if not r:
                return None
            cols = [d[0] for d in cur.description]
            return _row_from_db(dict(zip(cols, r)))


def _pg_get_recent(n: int) -> list[dict]:
    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT date, started_at, stages, overall_status "
                "FROM health_status ORDER BY date DESC LIMIT %s",
                (n,),
            )
            cols = [d[0] for d in cur.description]
            return [_row_from_db(dict(zip(cols, r))) for r in cur.fetchall()]
