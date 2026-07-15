"""Per-wolf fund manager run tracking in Supabase (`fund_manager_runs`)."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

_TABLE = "fund_manager_runs"
_STAGE_KEYS = ("shortlist", "holdings", "brain", "executor", "intents")


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
    statuses = [
        (stages.get(k) or {}).get("status")
        for k in _STAGE_KEYS
        if k in stages
    ]
    if not statuses:
        return "failed"
    if any(s == "failed" for s in statuses):
        if any(s == "success" for s in statuses):
            return "partial"
        return "failed"
    if all(s in ("success", "skipped") for s in statuses):
        return "success"
    return "partial"


def _row_from_db(row: dict) -> dict:
    stages = row.get("stages") or {}
    if isinstance(stages, str):
        stages = json.loads(stages)

    d = row.get("run_date")
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
        "run_date": d,
        "wolf_id": row.get("wolf_id"),
        "run_type": row.get("run_type"),
        "started_at": started,
        "finished_at": finished,
        "stages": stages,
        "overall_status": row.get("overall_status"),
        "error_detail": row.get("error_detail"),
        "selection_run_id": row.get("selection_run_id"),
    }


def start_wolf_run(
    wolf_id: str,
    *,
    run_date: date | str | None = None,
    run_type: str = "daily_review",
) -> dict:
    day_s = _day_str(run_date)
    now = datetime.now(timezone.utc)
    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_TABLE} (
                    run_date, wolf_id, run_type, started_at, stages, overall_status
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id, run_date, wolf_id, run_type, started_at, finished_at,
                          stages, overall_status, error_detail, selection_run_id
                """,
                (day_s, wolf_id, run_type, now, "{}", "running"),
            )
            r = cur.fetchone()
            cols = [d[0] for d in cur.description]
            row = _row_from_db(dict(zip(cols, r)))
    log.info("[FUND MANAGER] started run %s for %s", row["id"], wolf_id)
    return row


def update_wolf_stage(
    run_id: str,
    stage_key: str,
    *,
    status: str,
    detail: str = "",
) -> dict:
    row = get_wolf_run_by_id(run_id)
    if not row:
        raise RuntimeError(f"fund manager run not found: {run_id}")

    stages = _deep_merge(row.get("stages") or {}, {stage_key: {"status": status, "detail": detail}})
    overall = _compute_overall(stages) if status != "running" else row.get("overall_status") or "running"

    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET stages = %s::jsonb, overall_status = %s
                WHERE id = %s
                RETURNING id, run_date, wolf_id, run_type, started_at, finished_at,
                          stages, overall_status, error_detail, selection_run_id
                """,
                (json.dumps(stages), overall, run_id),
            )
            r = cur.fetchone()
            cols = [d[0] for d in cur.description]
            saved = _row_from_db(dict(zip(cols, r)))
    log.info(
        "[FUND MANAGER] %s stage %s → %s (%s)",
        row.get("wolf_id"),
        stage_key,
        status,
        detail[:80],
    )
    return saved


def finalize_wolf_run(
    run_id: str,
    *,
    overall_status: str | None = None,
    selection_run_id: int | None = None,
    error_detail: str | None = None,
) -> dict:
    row = get_wolf_run_by_id(run_id)
    if not row:
        raise RuntimeError(f"fund manager run not found: {run_id}")

    stages = row.get("stages") or {}
    overall = overall_status or _compute_overall(stages)
    now = datetime.now(timezone.utc)

    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET finished_at = %s,
                    overall_status = %s,
                    selection_run_id = COALESCE(%s, selection_run_id),
                    error_detail = COALESCE(%s, error_detail)
                WHERE id = %s
                RETURNING id, run_date, wolf_id, run_type, started_at, finished_at,
                          stages, overall_status, error_detail, selection_run_id
                """,
                (now, overall, selection_run_id, error_detail, run_id),
            )
            r = cur.fetchone()
            cols = [d[0] for d in cur.description]
            saved = _row_from_db(dict(zip(cols, r)))
    log.info(
        "[FUND MANAGER] finished %s for %s → %s",
        run_id,
        saved.get("wolf_id"),
        overall,
    )
    return saved


def get_wolf_run_by_id(run_id: str) -> dict | None:
    from db.connection import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, run_date, wolf_id, run_type, started_at, finished_at,
                           stages, overall_status, error_detail, selection_run_id
                    FROM {_TABLE}
                    WHERE id = %s
                    """,
                    (run_id,),
                )
                r = cur.fetchone()
                if not r:
                    return None
                cols = [d[0] for d in cur.description]
                return _row_from_db(dict(zip(cols, r)))
    except Exception as e:
        log.exception("get_wolf_run_by_id failed: %s", e)
        raise


def get_runs_for_day(run_date: date | str | None = None) -> list[dict]:
    day_s = _day_str(run_date)
    from db.connection import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, run_date, wolf_id, run_type, started_at, finished_at,
                           stages, overall_status, error_detail, selection_run_id
                    FROM {_TABLE}
                    WHERE run_date = %s
                    ORDER BY started_at DESC
                    """,
                    (day_s,),
                )
                cols = [d[0] for d in cur.description]
                return [_row_from_db(dict(zip(cols, r))) for r in cur.fetchall()]
    except Exception as e:
        log.exception("get_runs_for_day failed: %s", e)
        raise


def get_recent_day_summaries(n: int = 5) -> list[dict]:
    """One summary per calendar day (latest runs that day)."""
    from db.connection import get_connection

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT run_date,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE overall_status = 'success') AS ok,
                           COUNT(*) FILTER (WHERE overall_status = 'failed') AS failed,
                           COUNT(*) FILTER (WHERE overall_status = 'partial') AS partial
                    FROM {_TABLE}
                    GROUP BY run_date
                    ORDER BY run_date DESC
                    LIMIT %s
                    """,
                    (n,),
                )
                out: list[dict] = []
                for r in cur.fetchall():
                    run_date, total, ok, failed, partial = r
                    if hasattr(run_date, "isoformat"):
                        run_date = run_date.isoformat()
                    if failed and ok:
                        overall = "partial"
                    elif failed:
                        overall = "failed"
                    elif ok:
                        overall = "success"
                    elif partial:
                        overall = "partial"
                    else:
                        overall = "unknown"
                    out.append(
                        {
                            "date": run_date,
                            "total": int(total),
                            "ok": int(ok),
                            "failed": int(failed),
                            "partial": int(partial),
                            "overall": overall,
                        }
                    )
                return out
    except Exception as e:
        log.exception("get_recent_day_summaries failed: %s", e)
        raise
