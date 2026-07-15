"""
Phase E — structured health status store.

Each pipeline invocation creates a row in `health_runs` (multiple runs per day).
"""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parents[1]
load_dotenv(_REPO / ".env")

_supabase = None
_client_failed = False
_tls = threading.local()
_RUNS_TABLE = "health_runs"


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


def _set_active_run(run_id: str | None) -> None:
    _tls.run_id = run_id


def _get_active_run() -> str | None:
    return getattr(_tls, "run_id", None)


def _day_str(day: date | str | None) -> str:
    if day is None:
        day = date.today()
    return day.isoformat() if isinstance(day, date) else str(day)


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

    d = row.get("run_date") or row.get("date")
    if hasattr(d, "isoformat"):
        d = d.isoformat()

    started = row.get("started_at")
    if hasattr(started, "isoformat"):
        started = started.isoformat()

    finished = row.get("finished_at")
    if hasattr(finished, "isoformat"):
        finished = finished.isoformat()

    rid = row.get("id")
    if isinstance(rid, UUID):
        rid = str(rid)

    return {
        "id": rid,
        "date": d,
        "started_at": started,
        "finished_at": finished,
        "stages": stages,
        "overall_status": row.get("overall_status"),
    }


def get_status(day: date | str | None = None) -> dict | None:
    """Latest pipeline run for a calendar day (for today's live checklist)."""
    day_s = _day_str(day)

    client = _get_supabase()
    if client is not None:
        try:
            res = (
                client.table(_RUNS_TABLE)
                .select("*")
                .eq("run_date", day_s)
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return _row_from_db(rows[0]) if rows else None
        except Exception as e:
            print(f"[HEALTH STATUS] supabase get_status failed: {e}")

    return _get_latest_for_day(day_s)


def get_recent_statuses(n: int = 5) -> list[dict]:
    client = _get_supabase()
    if client is not None:
        try:
            res = (
                client.table(_RUNS_TABLE)
                .select("*")
                .order("started_at", desc=True)
                .limit(n)
                .execute()
            )
            return [_row_from_db(r) for r in (res.data or [])]
        except Exception as e:
            print(f"[HEALTH STATUS] supabase get_recent_statuses failed: {e}")

    return _pg_get_recent(n)


def start_run(day: date | None = None) -> dict:
    day_s = _day_str(day)
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "run_date": day_s,
        "started_at": now,
        "stages": {},
        "overall_status": "running",
    }
    saved = _insert_run(row, log_label="start_run")
    if saved.get("id"):
        _set_active_run(saved["id"])
    return saved


def update_stage(path: str, payload: dict, day: date | None = None) -> dict:
    """
    Merge a stage update into the active run's stages JSON.

    path examples: "fetch", "funnels.value", "batch_scoring.dip", "cache_saved"
    """
    existing = _resolve_run_for_update(day)
    if not existing:
        raise RuntimeError("no active health run; call start_run() first")
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
        "id": existing.get("id"),
        "run_date": existing.get("date") or _day_str(day),
        "started_at": existing.get("started_at")
        or datetime.now(timezone.utc).isoformat(),
        "finished_at": existing.get("finished_at"),
        "stages": stages,
        "overall_status": _compute_overall(stages),
    }
    return _update_run(row, log_label=f"stage:{path}")


def finalize(overall: str | None = None, day: date | None = None) -> dict:
    existing = _resolve_run_for_update(day)
    if not existing:
        latest = _get_latest_for_day(_day_str(day))
        if latest:
            return latest
        raise RuntimeError("no health run to finalize; call start_run() first")
    if existing.get("finished_at"):
        _set_active_run(None)
        return existing

    stages = existing.get("stages") or {}
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": existing.get("id"),
        "run_date": existing.get("date") or _day_str(day),
        "started_at": existing.get("started_at") or now,
        "finished_at": now,
        "stages": stages,
        "overall_status": overall or _compute_overall(stages),
    }
    saved = _update_run(row, log_label=f"finalize:{row['overall_status']}")
    _set_active_run(None)
    return saved


def _resolve_run_for_update(day: date | None = None) -> dict | None:
    active = _get_active_run()
    if active:
        row = _get_run_by_id(active)
        if row:
            return row

    day_s = _day_str(day)
    open_run = _get_latest_for_day(day_s, unfinished_only=True)
    if open_run:
        if open_run.get("id"):
            _set_active_run(open_run["id"])
        return open_run
    return None


def _get_latest_for_day(day_s: str, *, unfinished_only: bool = False) -> dict | None:
    client = _get_supabase()
    if client is not None:
        try:
            q = (
                client.table(_RUNS_TABLE)
                .select("*")
                .eq("run_date", day_s)
                .order("started_at", desc=True)
                .limit(1)
            )
            if unfinished_only:
                q = q.is_("finished_at", "null")
            res = q.execute()
            rows = res.data or []
            return _row_from_db(rows[0]) if rows else None
        except Exception as e:
            print(f"[HEALTH STATUS] supabase get_latest_for_day failed: {e}")

    return _pg_get_latest_for_day(day_s, unfinished_only=unfinished_only)


def _get_run_by_id(run_id: str) -> dict | None:
    client = _get_supabase()
    if client is not None:
        try:
            res = (
                client.table(_RUNS_TABLE)
                .select("*")
                .eq("id", run_id)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return _row_from_db(rows[0]) if rows else None
        except Exception as e:
            print(f"[HEALTH STATUS] supabase get_run_by_id failed: {e}")
    return _pg_get_run_by_id(run_id)


def _insert_run(row: dict, *, log_label: str) -> dict:
    client = _get_supabase()
    if client is not None:
        try:
            res = client.table(_RUNS_TABLE).insert(row).execute()
            data = (res.data or [row])[0]
            print(
                f"[HEALTH STATUS] insert {log_label} run_date={row['run_date']} "
                f"id={data.get('id') if isinstance(data, dict) else row.get('id')}"
            )
            return _row_from_db(data if isinstance(data, dict) else row)
        except Exception as e:
            print(f"[HEALTH STATUS] supabase insert failed ({e}); trying psycopg2")

    saved = _pg_insert(row)
    print(
        f"[HEALTH STATUS] insert {log_label} run_date={row['run_date']} "
        f"id={saved.get('id')} (psycopg2)"
    )
    return saved


def _update_run(row: dict, *, log_label: str) -> dict:
    run_id = row.get("id")
    if not run_id:
        raise RuntimeError("cannot update health run without id")

    patch = {
        "stages": row.get("stages") or {},
        "overall_status": row.get("overall_status"),
        "finished_at": row.get("finished_at"),
    }

    client = _get_supabase()
    if client is not None:
        try:
            res = (
                client.table(_RUNS_TABLE)
                .update(patch)
                .eq("id", run_id)
                .execute()
            )
            data = (res.data or [row])[0]
            print(
                f"[HEALTH STATUS] update {log_label} id={run_id} "
                f"overall={row.get('overall_status')}"
            )
            return _row_from_db(data if isinstance(data, dict) else row)
        except Exception as e:
            print(f"[HEALTH STATUS] supabase update failed ({e}); trying psycopg2")

    saved = _pg_update(run_id, patch)
    print(
        f"[HEALTH STATUS] update {log_label} id={run_id} "
        f"overall={row.get('overall_status')} (psycopg2)"
    )
    return saved


def _pg_insert(row: dict) -> dict:
    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO health_runs (run_date, started_at, stages, overall_status)
                VALUES (%s, %s, %s::jsonb, %s)
                RETURNING id, run_date, started_at, finished_at, stages, overall_status
                """,
                (
                    row["run_date"],
                    row.get("started_at"),
                    json.dumps(row.get("stages") or {}),
                    row.get("overall_status"),
                ),
            )
            r = cur.fetchone()
            cols = [d[0] for d in cur.description]
            return _row_from_db(dict(zip(cols, r)))


def _pg_update(run_id: str, patch: dict) -> dict:
    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE health_runs
                SET stages = %s::jsonb,
                    overall_status = %s,
                    finished_at = COALESCE(%s, finished_at)
                WHERE id = %s
                RETURNING id, run_date, started_at, finished_at, stages, overall_status
                """,
                (
                    json.dumps(patch.get("stages") or {}),
                    patch.get("overall_status"),
                    patch.get("finished_at"),
                    run_id,
                ),
            )
            r = cur.fetchone()
            if not r:
                raise RuntimeError(f"health run not found: {run_id}")
            cols = [d[0] for d in cur.description]
            return _row_from_db(dict(zip(cols, r)))


def _pg_get_run_by_id(run_id: str) -> dict | None:
    from db.connection import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, run_date, started_at, finished_at, stages, overall_status "
                    "FROM health_runs WHERE id = %s",
                    (run_id,),
                )
                r = cur.fetchone()
                if not r:
                    return None
                cols = [d[0] for d in cur.description]
                return _row_from_db(dict(zip(cols, r)))
    except Exception as e:
        print(f"[HEALTH STATUS] postgres get_run_by_id failed: {e}")
        raise


def _pg_get_latest_for_day(day_s: str, *, unfinished_only: bool = False) -> dict | None:
    from db.connection import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if unfinished_only:
                    cur.execute(
                        """
                        SELECT id, run_date, started_at, finished_at, stages, overall_status
                        FROM health_runs
                        WHERE run_date = %s AND finished_at IS NULL
                        ORDER BY started_at DESC
                        LIMIT 1
                        """,
                        (day_s,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, run_date, started_at, finished_at, stages, overall_status
                        FROM health_runs
                        WHERE run_date = %s
                        ORDER BY started_at DESC
                        LIMIT 1
                        """,
                        (day_s,),
                    )
                r = cur.fetchone()
                if not r:
                    return None
                cols = [d[0] for d in cur.description]
                return _row_from_db(dict(zip(cols, r)))
    except Exception as e:
        print(f"[HEALTH STATUS] postgres get_latest_for_day failed: {e}")
        raise


def _pg_get_recent(n: int) -> list[dict]:
    from db.connection import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, run_date, started_at, finished_at, stages, overall_status "
                    "FROM health_runs ORDER BY started_at DESC LIMIT %s",
                    (n,),
                )
                cols = [d[0] for d in cur.description]
                return [_row_from_db(dict(zip(cols, r))) for r in cur.fetchall()]
    except Exception as e:
        print(f"[HEALTH STATUS] postgres get_recent failed: {e}")
        raise
