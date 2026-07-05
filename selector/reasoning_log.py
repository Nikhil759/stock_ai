"""Collect human-readable reasoning one-liners for UI + console logs."""
from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)


class ReasoningLog:
    """Thread-safe trail of screening decisions. Each entry is shown in API
    responses (deploy progress / shortlist) and mirrored to Python logs."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, phase: str, message: str, **extra: Any) -> None:
        entry = {"phase": phase, "message": message, **extra}
        with self._lock:
            self._entries.append(entry)
        log.info("[%s] %s", phase.upper(), message)

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)

    def latest_for_phase(self, phase: str) -> str | None:
        with self._lock:
            for entry in reversed(self._entries):
                if entry.get("phase") == phase:
                    return entry.get("message")
        return None
