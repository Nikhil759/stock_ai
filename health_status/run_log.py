"""Buffer ingestion-run logs for persistence in health_runs.stages.run_logs."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

MAX_ENTRIES = 400
MAX_MESSAGE_CHARS = 2000

_tls = threading.local()
_ATTACHED: list[tuple[logging.Logger, logging.Handler]] = []
_LOGGERS = (
    "cron.morning_ingestion",
    "scoring.batch_scorer",
    "selector.llm.usage",
)


class RunLogBuffer:
    """In-memory log buffer for one health_runs row."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.entries: list[dict[str, Any]] = []
        self.truncated = False

    def append(
        self,
        level: str,
        logger_name: str,
        message: str,
        *,
        event: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if len(self.entries) >= MAX_ENTRIES:
            self.truncated = True
            return
        row: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "logger": logger_name,
            "message": (message or "")[:MAX_MESSAGE_CHARS],
        }
        if event:
            row["event"] = event
        if payload:
            row["payload"] = payload
        self.entries.append(row)

    def record_event(self, event: str, payload: dict[str, Any]) -> None:
        self.append("INFO", event, event, event=event, payload=payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "count": len(self.entries),
            "truncated": self.truncated,
            "entries": self.entries,
        }


class _RunLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        buf = get_run_log_buffer()
        if buf is None:
            return
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()

        event = None
        payload = None
        for marker in ("llm_call ", "llm_run_summary ", "llm_drift_summary "):
            if marker in message:
                event = marker.strip()
                idx = message.find("{")
                if idx >= 0:
                    try:
                        payload = json.loads(message[idx:])
                    except json.JSONDecodeError:
                        payload = None
                break

        buf.append(
            record.levelname,
            record.name,
            message,
            event=event,
            payload=payload,
        )


def get_run_log_buffer() -> RunLogBuffer | None:
    return getattr(_tls, "buffer", None)


def start_run_logging(run_id: str) -> RunLogBuffer:
    """Attach handlers that capture ingestion logs for this run."""
    stop_run_logging()
    buf = RunLogBuffer(run_id)
    _tls.buffer = buf
    handler = _RunLogHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        lg.addHandler(handler)
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)
        _ATTACHED.append((lg, handler))
    return buf


def stop_run_logging() -> None:
    while _ATTACHED:
        lg, handler = _ATTACHED.pop()
        try:
            lg.removeHandler(handler)
        except ValueError:
            pass
    _tls.buffer = None


def flush_run_logs() -> dict[str, Any] | None:
    """Persist buffered logs to the active health run."""
    buf = get_run_log_buffer()
    if buf is None:
        return None
    payload = buf.to_dict()
    from health_status.store import update_stage

    update_stage("run_logs", payload)
    return payload


def filter_run_logs(
    run_logs: dict[str, Any] | None,
    *,
    event: str | None = None,
    strategy: str | None = None,
    batch: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Filter persisted run log entries for the debug UI."""
    entries = list((run_logs or {}).get("entries") or [])
    out: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if event and row.get("event") != event and payload.get("event") != event:
            if event not in (row.get("message") or ""):
                continue
        if strategy:
            p_strat = payload.get("strategy")
            strat = strategy.lower()
            msg_lower = (row.get("message") or "").lower()
            if p_strat and str(p_strat).lower() != strat:
                continue
            if not p_strat and strat not in msg_lower:
                continue
        if batch:
            p_batch = payload.get("batch")
            if p_batch and p_batch != batch:
                continue
            if not p_batch and batch not in (row.get("message") or ""):
                continue
        out.append(row)
    if limit > 0:
        return out[-limit:]
    return out
