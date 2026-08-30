"""Tests for ingestion run log buffering."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "run_log",
    _REPO / "health_status" / "run_log.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
RunLogBuffer = _mod.RunLogBuffer
filter_run_logs = _mod.filter_run_logs


def test_run_log_buffer_and_filter():
    buf = RunLogBuffer("run-1")
    buf.record_event("llm_call", {"strategy": "value", "batch": "1/2", "symbols": ["ITC"]})
    buf.append("INFO", "scoring.batch_scorer", "[BATCH SCORING] Value: batch 1/2 [ITC]")
    payload = buf.to_dict()
    assert payload["run_id"] == "run-1"
    assert payload["count"] == 2

    filtered = filter_run_logs(payload, strategy="value", batch="1/2")
    assert len(filtered) == 2

    none = filter_run_logs(payload, strategy="dip")
    assert len(none) == 0


if __name__ == "__main__":
    test_run_log_buffer_and_filter()
    print("OK — run_log tests passed")
