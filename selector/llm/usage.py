"""Gemini usage_metadata extraction and daily-ingestion rollups."""
from __future__ import annotations

import json
import logging
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

PRICING_VERSION = "2026-08-gemini-paid"
BASELINE_WINDOW = 14
WARN_ZSCORE = 2.0
ALERT_ZSCORE = 3.0
# Rough Gemini token estimate for user-payload and system-instruction sizing.
CHARS_PER_TOKEN_EST = 4

_TOKEN_KEYS = (
    "prompt_tokens",
    "output_tokens",
    "cached_tokens",
    "thoughts_tokens",
    "total_tokens",
)

# USD per 1M tokens — Gemini Developer API paid tier (ai.google.dev/pricing).
# Cached input billed separately; thoughts billed at output rate.
_MODEL_RATES: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached": 0.03},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cached": 0.01},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cached": 0.025},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30, "cached": 0.01},
}
_DEFAULT_RATES = _MODEL_RATES["gemini-2.5-flash"]


def empty_tokens() -> dict[str, int]:
    return {k: 0 for k in _TOKEN_KEYS}


def _normalize_model(model: str) -> str:
    key = (model or "").strip().lower()
    key = re.sub(r"^models/", "", key)
    return key or "gemini-2.5-flash"


def _rates_for_model(model: str) -> dict[str, float]:
    key = _normalize_model(model)
    if key in _MODEL_RATES:
        return _MODEL_RATES[key]
    for prefix, rates in _MODEL_RATES.items():
        if key.startswith(prefix):
            return rates
    return _DEFAULT_RATES


def estimate_cost_usd(
    *,
    model: str,
    prompt_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    thoughts_tokens: int = 0,
) -> float:
    """Estimated Gemini API cost in USD (paid tier list prices)."""
    rates = _rates_for_model(model)
    prompt_tokens = max(0, int(prompt_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))
    cached_tokens = max(0, min(int(cached_tokens or 0), prompt_tokens))
    thoughts_tokens = max(0, int(thoughts_tokens or 0))

    regular_input = prompt_tokens - cached_tokens
    input_cost = regular_input * rates["input"]
    cached_cost = cached_tokens * rates["cached"]
    output_cost = (output_tokens + thoughts_tokens) * rates["output"]
    return (input_cost + cached_cost + output_cost) / 1_000_000


def estimate_cost_from_token_block(model: str, block: dict[str, Any]) -> float:
    return estimate_cost_usd(
        model=model,
        prompt_tokens=int(block.get("prompt_tokens", 0) or 0),
        output_tokens=int(block.get("output_tokens", 0) or 0),
        cached_tokens=int(block.get("cached_tokens", 0) or 0),
        thoughts_tokens=int(block.get("thoughts_tokens", 0) or 0),
    )


def attach_cost_estimates(payload: dict[str, Any]) -> dict[str, Any]:
    """Add estimated_cost_usd to totals, by_strategy, and batches."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    model = out.get("model") or "gemini-2.5-flash"

    totals = dict(out.get("totals") or {})
    totals["estimated_cost_usd"] = estimate_cost_from_token_block(model, totals)
    out["totals"] = totals

    by_strategy = {}
    for name, info in (out.get("by_strategy") or {}).items():
        if not isinstance(info, dict):
            continue
        row = dict(info)
        row["estimated_cost_usd"] = estimate_cost_from_token_block(model, row)
        by_strategy[name] = row
    out["by_strategy"] = by_strategy

    batches = []
    for batch in out.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        row = dict(batch)
        row["estimated_cost_usd"] = estimate_cost_from_token_block(model, row)
        batches.append(row)
    out["batches"] = batches
    return enrich_derived_metrics(out)


def _safe_div(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def estimate_tokens_from_chars(chars: int) -> int:
    return max(0, int(round(float(chars or 0) / CHARS_PER_TOKEN_EST)))


def system_prompt_overhead(model: str, system_prompt_chars: int) -> dict[str, Any]:
    """Estimated fixed system-instruction tokens/cost for one batch call."""
    chars = max(0, int(system_prompt_chars or 0))
    est_tokens = estimate_tokens_from_chars(chars)
    est_cost = estimate_cost_usd(model=model, prompt_tokens=est_tokens)
    return {
        "system_prompt_chars": chars,
        "system_prompt_estimated_tokens": est_tokens,
        "system_prompt_estimated_cost_usd": est_cost,
    }


def _apply_system_overhead(row: dict[str, Any]) -> dict[str, Any]:
    model = row.get("model") or "gemini-2.5-flash"
    chars = int(row.get("system_prompt_chars", 0) or 0)
    if chars > 0:
        row.update(system_prompt_overhead(model, chars))
    else:
        row.setdefault("system_prompt_chars", 0)
        row.setdefault("system_prompt_estimated_tokens", 0)
        row.setdefault("system_prompt_estimated_cost_usd", 0.0)
    return row


def _aggregate_system_overhead(batches: list[dict[str, Any]]) -> dict[str, Any]:
    chars = sum(int(b.get("system_prompt_chars", 0) or 0) for b in batches)
    tokens = sum(int(b.get("system_prompt_estimated_tokens", 0) or 0) for b in batches)
    cost = sum(float(b.get("system_prompt_estimated_cost_usd", 0) or 0) for b in batches)
    calls = len(batches)
    return {
        "system_prompt_chars": chars,
        "system_prompt_estimated_tokens": tokens,
        "system_prompt_estimated_cost_usd": cost,
        "avg_system_prompt_estimated_cost_usd": _safe_div(cost, float(calls)) if calls else None,
    }


def _aggregate_payload(batches: list[dict[str, Any]], total_symbols: int) -> dict[str, Any]:
    payload_chars_total = sum(int(b.get("payload_chars", 0) or 0) for b in batches)
    payload_tokens_total = sum(int(b.get("payload_tokens", 0) or 0) for b in batches)
    if not payload_tokens_total and payload_chars_total:
        payload_tokens_total = estimate_tokens_from_chars(payload_chars_total)
    return {
        "payload_chars_total": payload_chars_total,
        "payload_tokens_total": payload_tokens_total,
        "payload_chars_per_symbol": _safe_div(float(payload_chars_total), float(total_symbols)) if total_symbols else None,
        "payload_tokens_per_symbol": _safe_div(float(payload_tokens_total), float(total_symbols)) if total_symbols else None,
        "avg_payload_chars_per_batch": _safe_div(float(payload_chars_total), float(len(batches))) if batches else None,
        "avg_payload_tokens_per_batch": _safe_div(float(payload_tokens_total), float(len(batches))) if batches else None,
    }


def _enrich_batch_row(batch: dict[str, Any]) -> dict[str, Any]:
    row = dict(batch)
    symbols = row.get("symbols") or []
    if isinstance(symbols, str):
        symbol_count = len([s for s in symbols.split(",") if s.strip()]) or 1
    else:
        symbol_count = max(len(symbols), 1)
    row["symbol_count"] = symbol_count
    cost = float(row.get("estimated_cost_usd", 0) or 0)
    tokens = int(row.get("total_tokens", 0) or 0)
    prompt = int(row.get("prompt_tokens", 0) or 0)
    output = int(row.get("output_tokens", 0) or 0)
    thoughts = int(row.get("thoughts_tokens", 0) or 0)
    row["cost_per_symbol_usd"] = _safe_div(cost, symbol_count)
    row["tokens_per_symbol"] = _safe_div(float(tokens), float(symbol_count))
    row["prompt_tokens_per_symbol"] = _safe_div(float(prompt), float(symbol_count))
    row["output_tokens_per_symbol"] = _safe_div(float(output + thoughts), float(symbol_count))
    row["cost_per_1k_tokens_usd"] = _safe_div(cost * 1000, float(tokens)) if tokens else None
    payload_chars = int(row.get("payload_chars", 0) or 0)
    payload_tokens = int(row.get("payload_tokens", 0) or 0) or estimate_tokens_from_chars(payload_chars)
    row["payload_chars"] = payload_chars
    row["payload_tokens"] = payload_tokens
    row["payload_chars_per_symbol"] = _safe_div(float(payload_chars), float(symbol_count))
    row["payload_tokens_per_symbol"] = _safe_div(float(payload_tokens), float(symbol_count))
    row = _apply_system_overhead(row)
    return row


def _enrich_strategy_row(name: str, info: dict[str, Any], batches: list[dict[str, Any]]) -> dict[str, Any]:
    row = dict(info)
    calls = int(row.get("calls", 0) or 0)
    cost = float(row.get("estimated_cost_usd", 0) or 0)
    tokens = int(row.get("total_tokens", 0) or 0)
    candidates = int(row.get("candidates_scored", 0) or 0)
    survivors = int(row.get("survivors", 0) or 0)
    strat_batches = [b for b in batches if b.get("strategy") == name]

    row["avg_cost_per_batch_usd"] = _safe_div(cost, float(calls)) if calls else None
    row["avg_tokens_per_batch"] = _safe_div(float(tokens), float(calls)) if calls else None
    row["avg_prompt_tokens_per_batch"] = _safe_div(float(row.get("prompt_tokens", 0) or 0), float(calls)) if calls else None
    row["avg_output_tokens_per_batch"] = _safe_div(
        float(int(row.get("output_tokens", 0) or 0) + int(row.get("thoughts_tokens", 0) or 0)),
        float(calls),
    ) if calls else None
    row["avg_elapsed_ms_per_batch"] = _safe_div(
        float(row.get("elapsed_ms", 0) or 0), float(calls)
    ) if calls else None
    row["cost_per_candidate_usd"] = _safe_div(cost, float(candidates)) if candidates else None
    row["cost_per_survivor_usd"] = _safe_div(cost, float(survivors)) if survivors else None
    row["retry_rate"] = _safe_div(float(row.get("retries", 0) or 0), float(calls)) if calls else None

    sym_counts = [int(b.get("symbol_count", 0) or 0) for b in strat_batches if b.get("symbol_count")]
    total_symbols = sum(sym_counts) if sym_counts else candidates
    row["cost_per_symbol_usd"] = _safe_div(cost, float(total_symbols)) if total_symbols else None
    row["tokens_per_symbol"] = _safe_div(float(tokens), float(total_symbols)) if total_symbols else None
    row["prompt_tokens_per_symbol"] = _safe_div(float(row.get("prompt_tokens", 0) or 0), float(total_symbols)) if total_symbols else None
    row["output_tokens_per_symbol"] = _safe_div(
        float(int(row.get("output_tokens", 0) or 0) + int(row.get("thoughts_tokens", 0) or 0)),
        float(total_symbols),
    ) if total_symbols else None
    row.update(_aggregate_system_overhead(strat_batches))
    row.update(_aggregate_payload(strat_batches, total_symbols))
    return row


def enrich_derived_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Add per-batch and per-strategy normalized cost/token metrics."""
    out = dict(payload)
    model = out.get("model") or "gemini-2.5-flash"
    batches = [
        _enrich_batch_row({**b, "model": model})
        for b in (out.get("batches") or [])
        if isinstance(b, dict)
    ]
    out["batches"] = batches

    by_strategy = {}
    for name, info in (out.get("by_strategy") or {}).items():
        if isinstance(info, dict):
            by_strategy[name] = _enrich_strategy_row(name, info, batches)
    out["by_strategy"] = by_strategy

    totals = dict(out.get("totals") or {})
    calls = int(totals.get("calls", 0) or 0)
    cost = float(totals.get("estimated_cost_usd", 0) or 0)
    tokens = int(totals.get("total_tokens", 0) or 0)
    total_symbols = sum(int(b.get("symbol_count", 0) or 0) for b in batches)
    totals["avg_cost_per_batch_usd"] = _safe_div(cost, float(calls)) if calls else None
    totals["avg_tokens_per_batch"] = _safe_div(float(tokens), float(calls)) if calls else None
    totals["avg_prompt_tokens_per_batch"] = _safe_div(float(totals.get("prompt_tokens", 0) or 0), float(calls)) if calls else None
    totals["avg_output_tokens_per_batch"] = _safe_div(
        float(int(totals.get("output_tokens", 0) or 0) + int(totals.get("thoughts_tokens", 0) or 0)),
        float(calls),
    ) if calls else None
    totals["cost_per_symbol_usd"] = _safe_div(cost, float(total_symbols)) if total_symbols else None
    totals["tokens_per_symbol"] = _safe_div(float(tokens), float(total_symbols)) if total_symbols else None
    totals["prompt_tokens_per_symbol"] = _safe_div(float(totals.get("prompt_tokens", 0) or 0), float(total_symbols)) if total_symbols else None
    totals["output_tokens_per_symbol"] = _safe_div(
        float(int(totals.get("output_tokens", 0) or 0) + int(totals.get("thoughts_tokens", 0) or 0)),
        float(total_symbols),
    ) if total_symbols else None
    totals["retry_rate"] = _safe_div(float(totals.get("retries", 0) or 0), float(calls)) if calls else None
    totals.update(_aggregate_system_overhead(batches))
    totals.update(_aggregate_payload(batches, total_symbols))
    out["totals"] = totals
    out["pricing_version"] = out.get("pricing_version") or PRICING_VERSION
    return out


def aggregate_llm_batches(
    batches: list[dict[str, Any]],
    *,
    model: str = "",
) -> dict[str, Any]:
    """Roll up batch rows (possibly from multiple ingestion runs) into totals + strategies."""
    model = model or "gemini-2.5-flash"
    enriched: list[dict[str, Any]] = []
    for batch in batches or []:
        if not isinstance(batch, dict):
            continue
        row = dict(batch)
        if row.get("estimated_cost_usd") in (None, 0, 0.0) and int(row.get("total_tokens", 0) or 0) > 0:
            row["estimated_cost_usd"] = estimate_cost_from_token_block(model, row)
        enriched.append(_enrich_batch_row({**row, "model": model}))

    by_strategy: dict[str, dict[str, Any]] = {}
    for batch in enriched:
        name = str(batch.get("strategy") or "?")
        strat = by_strategy.setdefault(
            name,
            {
                "calls": 0,
                "retries": 0,
                "elapsed_ms": 0,
                "estimated_cost_usd": 0.0,
                **empty_tokens(),
            },
        )
        strat["calls"] += 1
        strat["retries"] += max(0, int(batch.get("attempts", 1) or 1) - 1)
        strat["elapsed_ms"] += int(batch.get("elapsed_ms", 0) or 0)
        strat["estimated_cost_usd"] = float(strat.get("estimated_cost_usd", 0) or 0) + float(
            batch.get("estimated_cost_usd", 0) or 0
        )
        add_tokens(strat, {key: int(batch.get(key, 0) or 0) for key in _TOKEN_KEYS})

    strategies: list[dict[str, Any]] = []
    for name in ("value", "winners", "box", "dip"):
        if name not in by_strategy:
            continue
        info = by_strategy[name]
        strat_batches = [b for b in enriched if b.get("strategy") == name]
        strategies.append({"name": name, **_enrich_strategy_row(name, info, strat_batches)})

    totals: dict[str, Any] = {
        "calls": len(enriched),
        "retries": sum(max(0, int(b.get("attempts", 1) or 1) - 1) for b in enriched),
        "elapsed_ms": sum(int(b.get("elapsed_ms", 0) or 0) for b in enriched),
        **empty_tokens(),
    }
    for batch in enriched:
        add_tokens(totals, {key: int(batch.get(key, 0) or 0) for key in _TOKEN_KEYS})
    totals["estimated_cost_usd"] = sum(float(b.get("estimated_cost_usd", 0) or 0) for b in enriched)

    calls = int(totals.get("calls", 0) or 0)
    cost = float(totals.get("estimated_cost_usd", 0) or 0)
    tokens = int(totals.get("total_tokens", 0) or 0)
    total_symbols = sum(int(b.get("symbol_count", 0) or 0) for b in enriched)
    totals["avg_cost_per_batch_usd"] = _safe_div(cost, float(calls)) if calls else None
    totals["avg_tokens_per_batch"] = _safe_div(float(tokens), float(calls)) if calls else None
    totals["avg_prompt_tokens_per_batch"] = _safe_div(float(totals.get("prompt_tokens", 0) or 0), float(calls)) if calls else None
    totals["avg_output_tokens_per_batch"] = _safe_div(
        float(int(totals.get("output_tokens", 0) or 0) + int(totals.get("thoughts_tokens", 0) or 0)),
        float(calls),
    ) if calls else None
    totals["cost_per_symbol_usd"] = _safe_div(cost, float(total_symbols)) if total_symbols else None
    totals["tokens_per_symbol"] = _safe_div(float(tokens), float(total_symbols)) if total_symbols else None
    totals["prompt_tokens_per_symbol"] = _safe_div(float(totals.get("prompt_tokens", 0) or 0), float(total_symbols)) if total_symbols else None
    totals["output_tokens_per_symbol"] = _safe_div(
        float(int(totals.get("output_tokens", 0) or 0) + int(totals.get("thoughts_tokens", 0) or 0)),
        float(total_symbols),
    ) if total_symbols else None
    totals["retry_rate"] = _safe_div(float(totals.get("retries", 0) or 0), float(calls)) if calls else None
    totals.update(_aggregate_system_overhead(enriched))
    totals.update(_aggregate_payload(enriched, total_symbols))

    return {"totals": totals, "strategies": strategies}


def _median(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return float(statistics.median(clean))


def _stdev(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if len(clean) < 2:
        return None
    return float(statistics.stdev(clean))


def extract_run_llm_snapshot(stages: dict | None) -> dict[str, Any] | None:
    """Normalized llm_usage block from a health_runs stages dict."""
    raw = (stages or {}).get("llm_usage")
    if not isinstance(raw, dict) or not raw.get("totals"):
        return None
    return enrich_derived_metrics(attach_cost_estimates(raw))


def build_llm_baselines(recent_runs: list[dict[str, Any]], *, window: int = BASELINE_WINDOW) -> dict[str, Any]:
    """
    Rolling baselines from prior ingestion runs (median + stdev).

    recent_runs: health_runs rows newest-first; skips runs without llm_usage.
    """
    snapshots: list[dict[str, Any]] = []
    for run in recent_runs[:window]:
        snap = extract_run_llm_snapshot(run.get("stages") or {})
        if snap:
            snapshots.append(snap)

    if not snapshots:
        return {"window": window, "sample_size": 0}

    run_costs = [
        float((s.get("totals") or {}).get("estimated_cost_usd", 0) or 0) for s in snapshots
    ]
    run_tokens = [
        float((s.get("totals") or {}).get("total_tokens", 0) or 0) for s in snapshots
    ]
    run_avg_batch_costs = [
        float((s.get("totals") or {}).get("avg_cost_per_batch_usd", 0) or 0) for s in snapshots
    ]
    run_avg_batch_tokens = [
        float((s.get("totals") or {}).get("avg_tokens_per_batch", 0) or 0) for s in snapshots
    ]
    run_cost_per_symbol = [
        float((s.get("totals") or {}).get("cost_per_symbol_usd", 0) or 0) for s in snapshots
    ]
    run_tokens_per_symbol = [
        float((s.get("totals") or {}).get("tokens_per_symbol", 0) or 0) for s in snapshots
    ]
    run_prompt_per_symbol = [
        float((s.get("totals") or {}).get("prompt_tokens_per_symbol", 0) or 0) for s in snapshots
    ]
    run_output_per_symbol = [
        float((s.get("totals") or {}).get("output_tokens_per_symbol", 0) or 0) for s in snapshots
    ]
    run_payload_per_symbol = [
        float((s.get("totals") or {}).get("payload_tokens_per_symbol", 0) or 0) for s in snapshots
    ]
    run_system_cost = [
        float((s.get("totals") or {}).get("system_prompt_estimated_cost_usd", 0) or 0) for s in snapshots
    ]

    strategy_metrics: dict[str, dict[str, list[float]]] = {}
    batch_metrics: dict[str, list[float]] = {
        "estimated_cost_usd": [],
        "cost_per_symbol_usd": [],
        "payload_tokens_per_symbol": [],
        "tokens_per_symbol": [],
        "prompt_tokens_per_symbol": [],
        "output_tokens_per_symbol": [],
        "elapsed_ms": [],
    }

    for snap in snapshots:
        for name, info in (snap.get("by_strategy") or {}).items():
            if not isinstance(info, dict):
                continue
            bucket = strategy_metrics.setdefault(name, {})
            for key in (
                "estimated_cost_usd",
                "avg_cost_per_batch_usd",
                "avg_tokens_per_batch",
                "avg_prompt_tokens_per_batch",
                "avg_output_tokens_per_batch",
                "cost_per_symbol_usd",
                "tokens_per_symbol",
                "prompt_tokens_per_symbol",
                "output_tokens_per_symbol",
                "payload_tokens_per_symbol",
                "system_prompt_estimated_cost_usd",
                "retry_rate",
            ):
                val = info.get(key)
                if val is not None:
                    bucket.setdefault(key, []).append(float(val))
        for batch in snap.get("batches") or []:
            if not isinstance(batch, dict):
                continue
            for key in batch_metrics:
                val = batch.get(key)
                if val is not None:
                    batch_metrics[key].append(float(val))

    def _stat_block(values: list[float]) -> dict[str, float | None]:
        med = _median(values)
        sd = _stdev(values)
        return {"median": med, "stdev": sd, "n": len(values)}

    by_strategy = {
        name: {metric: _stat_block(vals) for metric, vals in metrics.items()}
        for name, metrics in strategy_metrics.items()
    }

    return {
        "window": window,
        "sample_size": len(snapshots),
        "totals": {
            "estimated_cost_usd": _stat_block(run_costs),
            "total_tokens": _stat_block(run_tokens),
            "avg_cost_per_batch_usd": _stat_block(run_avg_batch_costs),
            "avg_tokens_per_batch": _stat_block(run_avg_batch_tokens),
            "cost_per_symbol_usd": _stat_block(run_cost_per_symbol),
            "tokens_per_symbol": _stat_block(run_tokens_per_symbol),
            "prompt_tokens_per_symbol": _stat_block(run_prompt_per_symbol),
            "output_tokens_per_symbol": _stat_block(run_output_per_symbol),
            "payload_tokens_per_symbol": _stat_block(run_payload_per_symbol),
            "system_prompt_estimated_cost_usd": _stat_block(run_system_cost),
        },
        "by_strategy": by_strategy,
        "batch": {k: _stat_block(v) for k, v in batch_metrics.items()},
    }


def compare_to_baseline(value: float | None, baseline: dict[str, Any] | None) -> dict[str, Any]:
    """Return delta_pct, z_score, and severity (ok|warn|alert) vs a baseline stat block."""
    if value is None or not baseline:
        return {"delta_pct": None, "z_score": None, "severity": "ok"}
    median = baseline.get("median")
    stdev = baseline.get("stdev")
    if median is None:
        return {"delta_pct": None, "z_score": None, "severity": "ok"}

    delta_pct = ((float(value) - float(median)) / float(median) * 100) if median else None
    z_score = None
    severity = "ok"
    if stdev and stdev > 0:
        z_score = (float(value) - float(median)) / float(stdev)
        if abs(z_score) >= ALERT_ZSCORE:
            severity = "alert"
        elif abs(z_score) >= WARN_ZSCORE:
            severity = "warn"
    elif delta_pct is not None and abs(delta_pct) >= 50:
        severity = "warn"

    return {
        "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
        "z_score": round(z_score, 2) if z_score is not None else None,
        "severity": severity,
        "baseline_median": median,
    }


def _baseline_get(baselines: dict[str, Any] | None, *path: str) -> dict[str, Any] | None:
    if not baselines:
        return None
    cur: Any = baselines
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur if isinstance(cur, dict) else None


def _max_severity(*severities: str) -> str:
    if "alert" in severities:
        return "alert"
    if "warn" in severities:
        return "warn"
    return "ok"


def build_llm_drift_report(
    snapshot: dict[str, Any],
    baselines: dict[str, Any] | None,
) -> dict[str, Any]:
    """Drift vs rolling baselines for run totals, strategies, and individual batches."""
    totals = snapshot.get("totals") or {}
    by_strategy = snapshot.get("by_strategy") or {}

    totals_drifts = {
        "estimated_cost_usd": compare_to_baseline(
            float(totals.get("estimated_cost_usd", 0) or 0),
            _baseline_get(baselines, "totals", "estimated_cost_usd"),
        ),
        "payload_tokens_per_symbol": compare_to_baseline(
            totals.get("payload_tokens_per_symbol"),
            _baseline_get(baselines, "totals", "payload_tokens_per_symbol"),
        ),
        "tokens_per_symbol": compare_to_baseline(
            totals.get("tokens_per_symbol"),
            _baseline_get(baselines, "totals", "tokens_per_symbol"),
        ),
        "system_prompt_estimated_cost_usd": compare_to_baseline(
            totals.get("system_prompt_estimated_cost_usd"),
            _baseline_get(baselines, "totals", "system_prompt_estimated_cost_usd"),
        ),
        "prompt_tokens_per_symbol": compare_to_baseline(
            totals.get("prompt_tokens_per_symbol"),
            _baseline_get(baselines, "totals", "prompt_tokens_per_symbol"),
        ),
        "output_tokens_per_symbol": compare_to_baseline(
            totals.get("output_tokens_per_symbol"),
            _baseline_get(baselines, "totals", "output_tokens_per_symbol"),
        ),
        "cost_per_symbol_usd": compare_to_baseline(
            totals.get("cost_per_symbol_usd"),
            _baseline_get(baselines, "totals", "cost_per_symbol_usd"),
        ),
        "avg_cost_per_batch_usd": compare_to_baseline(
            totals.get("avg_cost_per_batch_usd"),
            _baseline_get(baselines, "totals", "avg_cost_per_batch_usd"),
        ),
        "avg_tokens_per_batch": compare_to_baseline(
            totals.get("avg_tokens_per_batch"),
            _baseline_get(baselines, "totals", "avg_tokens_per_batch"),
        ),
    }

    strategies: dict[str, dict[str, Any]] = {}
    for name in ("value", "winners", "box", "dip"):
        info = by_strategy.get(name)
        if not isinstance(info, dict):
            continue
        drifts = {
            "payload_tokens_per_symbol": compare_to_baseline(
                info.get("payload_tokens_per_symbol"),
                _baseline_get(baselines, "by_strategy", name, "payload_tokens_per_symbol"),
            ),
            "tokens_per_symbol": compare_to_baseline(
                info.get("tokens_per_symbol"),
                _baseline_get(baselines, "by_strategy", name, "tokens_per_symbol"),
            ),
            "prompt_tokens_per_symbol": compare_to_baseline(
                info.get("prompt_tokens_per_symbol"),
                _baseline_get(baselines, "by_strategy", name, "prompt_tokens_per_symbol"),
            ),
            "output_tokens_per_symbol": compare_to_baseline(
                info.get("output_tokens_per_symbol"),
                _baseline_get(baselines, "by_strategy", name, "output_tokens_per_symbol"),
            ),
            "cost_per_symbol_usd": compare_to_baseline(
                info.get("cost_per_symbol_usd"),
                _baseline_get(baselines, "by_strategy", name, "cost_per_symbol_usd"),
            ),
            "estimated_cost_usd": compare_to_baseline(
                float(info.get("estimated_cost_usd", 0) or 0),
                _baseline_get(baselines, "by_strategy", name, "estimated_cost_usd"),
            ),
            "system_prompt_estimated_cost_usd": compare_to_baseline(
                info.get("system_prompt_estimated_cost_usd"),
                _baseline_get(baselines, "by_strategy", name, "system_prompt_estimated_cost_usd"),
            ),
        }
        strategies[name] = {
            "drifts": drifts,
            "severity": _max_severity(*(d.get("severity", "ok") for d in drifts.values())),
        }

    anomalous_batches: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    for batch in snapshot.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        payload_drift = compare_to_baseline(
            batch.get("payload_tokens_per_symbol"),
            _baseline_get(baselines, "batch", "payload_tokens_per_symbol"),
        )
        token_drift = compare_to_baseline(
            batch.get("tokens_per_symbol"),
            _baseline_get(baselines, "batch", "tokens_per_symbol"),
        )
        prompt_drift = compare_to_baseline(
            batch.get("prompt_tokens_per_symbol"),
            _baseline_get(baselines, "batch", "prompt_tokens_per_symbol"),
        )
        output_drift = compare_to_baseline(
            batch.get("output_tokens_per_symbol"),
            _baseline_get(baselines, "batch", "output_tokens_per_symbol"),
        )
        cost_drift = compare_to_baseline(
            batch.get("cost_per_symbol_usd"),
            _baseline_get(baselines, "batch", "cost_per_symbol_usd"),
        )
        latency_drift = compare_to_baseline(
            float(batch.get("elapsed_ms", 0) or 0),
            _baseline_get(baselines, "batch", "elapsed_ms"),
        )
        severity = _max_severity(
            payload_drift.get("severity", "ok"),
            token_drift.get("severity", "ok"),
            prompt_drift.get("severity", "ok"),
            output_drift.get("severity", "ok"),
            cost_drift.get("severity", "ok"),
            latency_drift.get("severity", "ok"),
        )
        if batch.get("status") in ("failed", "degraded"):
            severity = _max_severity(severity, "warn")

        batch_report = {
            "strategy": batch.get("strategy"),
            "batch": batch.get("batch"),
            "symbols": batch.get("symbols"),
            "symbol_count": batch.get("symbol_count"),
            "payload_tokens_per_symbol": batch.get("payload_tokens_per_symbol"),
            "payload_chars_per_symbol": batch.get("payload_chars_per_symbol"),
            "payload_breakdown": batch.get("payload_breakdown"),
            "tokens_per_symbol": batch.get("tokens_per_symbol"),
            "prompt_tokens_per_symbol": batch.get("prompt_tokens_per_symbol"),
            "output_tokens_per_symbol": batch.get("output_tokens_per_symbol"),
            "system_prompt_estimated_cost_usd": batch.get("system_prompt_estimated_cost_usd"),
            "elapsed_ms": batch.get("elapsed_ms"),
            "drifts": {
                "payload_tokens_per_symbol": payload_drift,
                "tokens_per_symbol": token_drift,
                "prompt_tokens_per_symbol": prompt_drift,
                "output_tokens_per_symbol": output_drift,
                "cost_per_symbol_usd": cost_drift,
                "elapsed_ms": latency_drift,
            },
            "severity": severity,
        }
        batches.append(batch_report)
        if severity != "ok":
            anomalous_batches.append(batch_report)

    run_severity = _max_severity(
        totals_drifts["payload_tokens_per_symbol"].get("severity", "ok"),
        totals_drifts["tokens_per_symbol"].get("severity", "ok"),
        totals_drifts["cost_per_symbol_usd"].get("severity", "ok"),
        *(s["severity"] for s in strategies.values()),
        *(b["severity"] for b in anomalous_batches),
    )

    return {
        "baseline_sample_size": int((baselines or {}).get("sample_size", 0) or 0),
        "baseline_window": int((baselines or {}).get("window", BASELINE_WINDOW) or BASELINE_WINDOW),
        "totals": totals_drifts,
        "strategies": strategies,
        "batches": batches,
        "anomalous_batches": anomalous_batches,
        "max_severity": run_severity,
    }


def log_llm_drift_summary(snapshot: dict[str, Any], baselines: dict[str, Any] | None) -> dict[str, Any]:
    """Emit structured drift report after an ingestion run."""
    report = build_llm_drift_report(snapshot, baselines)
    if report["baseline_sample_size"] == 0:
        return report

    log.info(
        "llm_drift_summary %s",
        json.dumps(
            {
                "event": "llm_drift_summary",
                "phase": snapshot.get("phase"),
                "model": snapshot.get("model"),
                "baseline_sample_size": report["baseline_sample_size"],
                "max_severity": report["max_severity"],
                "totals": {
                    k: {
                        "delta_pct": v.get("delta_pct"),
                        "z_score": v.get("z_score"),
                        "severity": v.get("severity"),
                    }
                    for k, v in report["totals"].items()
                },
                "strategies": {
                    name: {
                        "severity": info.get("severity"),
                        "tokens_per_symbol": info["drifts"]["tokens_per_symbol"],
                        "payload_tokens_per_symbol": info["drifts"]["payload_tokens_per_symbol"],
                    }
                    for name, info in report["strategies"].items()
                },
                "anomalous_batches": [
                    {
                        "strategy": b.get("strategy"),
                        "batch": b.get("batch"),
                        "severity": b.get("severity"),
                        "tokens_per_symbol": b["drifts"]["tokens_per_symbol"],
                        "payload_tokens_per_symbol": b["drifts"]["payload_tokens_per_symbol"],
                        "elapsed_ms": b["drifts"]["elapsed_ms"],
                    }
                    for b in report["anomalous_batches"]
                ],
            },
            default=str,
        ),
    )
    if report["max_severity"] == "alert":
        log.warning(
            "[LLM DRIFT] alert severity — payload_tokens_per_symbol z=%s",
            report["totals"]["payload_tokens_per_symbol"].get("z_score"),
        )
    elif report["max_severity"] == "warn":
        log.warning(
            "[LLM DRIFT] warn severity — review anomalous batches (%d)",
            len(report["anomalous_batches"]),
        )
    return report


def finalize_llm_usage_payload(
    payload: dict[str, Any],
    baselines: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach drift_report and per-batch drift_debug before persisting."""
    snap = enrich_derived_metrics(dict(payload))
    report = build_llm_drift_report(snap, baselines)
    out = dict(snap)
    out["drift_report"] = {
        "baseline_sample_size": report.get("baseline_sample_size"),
        "baseline_window": report.get("baseline_window"),
        "max_severity": report.get("max_severity"),
        "totals": report.get("totals"),
        "strategies": report.get("strategies"),
        "anomalous_batches": report.get("anomalous_batches"),
    }
    drift_by_key = {
        (b.get("strategy"), b.get("batch")): b for b in (report.get("batches") or [])
    }
    batches = []
    for batch in out.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        row = dict(batch)
        key = (row.get("strategy"), row.get("batch"))
        bd = drift_by_key.get(key, {})
        drifts = bd.get("drifts") or {}
        row["drift_debug"] = {
            "severity": bd.get("severity"),
            "drifts": drifts,
            "baseline_sample_size": report.get("baseline_sample_size"),
            "payload_tokens_per_symbol": row.get("payload_tokens_per_symbol"),
            "payload_breakdown": row.get("payload_breakdown"),
        }
        batches.append(row)
    out["batches"] = batches
    return out


def find_batch_in_llm_usage(
    llm_usage: dict[str, Any] | None,
    *,
    strategy: str,
    batch: str,
) -> dict[str, Any] | None:
    if not isinstance(llm_usage, dict):
        return None
    for row in llm_usage.get("batches") or []:
        if not isinstance(row, dict):
            continue
        if row.get("strategy") == strategy and row.get("batch") == batch:
            return row
    return None


def build_symbol_section_baselines(
    recent_runs: list[dict[str, Any]],
    strategy: str,
    *,
    exclude_run_id: str | None = None,
    window: int = BASELINE_WINDOW,
) -> dict[str, dict[str, dict[str, Any]]]:
    """
    Per-symbol section char baselines from prior ingestion runs.

    Returns: symbol -> section -> {median, n}
    """
    buckets: dict[str, dict[str, list[float]]] = {}
    runs_used = 0
    for run in recent_runs:
        if exclude_run_id and run.get("id") == exclude_run_id:
            continue
        snap = extract_run_llm_snapshot(run.get("stages") or {})
        if not snap:
            continue
        for batch in snap.get("batches") or []:
            if not isinstance(batch, dict):
                continue
            if batch.get("strategy") != strategy:
                continue
            pb = batch.get("payload_breakdown") or {}
            per_sym = pb.get("per_symbol") if isinstance(pb.get("per_symbol"), dict) else {}
            for sym, sections in per_sym.items():
                if not isinstance(sections, dict):
                    continue
                sym_key = str(sym).upper()
                sym_bucket = buckets.setdefault(sym_key, {})
                for sec, chars in sections.items():
                    try:
                        sym_bucket.setdefault(str(sec), []).append(float(chars))
                    except (TypeError, ValueError):
                        continue
        runs_used += 1
        if runs_used >= window:
            break

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for sym, secs in buckets.items():
        out[sym] = {}
        for sec, vals in secs.items():
            med = _median(vals)
            out[sym][sec] = {"median": med, "n": len(vals)}
    return out


def analyze_batch_drift(
    batch_row: dict[str, Any],
    symbol_baselines: dict[str, dict[str, dict[str, Any]]] | None,
    *,
    batch_size: int = 8,
) -> dict[str, Any]:
    """Per-symbol section contributors and human-readable drift reasons."""
    symbols = batch_row.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    symbol_count = int(batch_row.get("symbol_count", 0) or 0) or len(symbols)
    attempts = int(batch_row.get("attempts", 1) or 1)
    pb = batch_row.get("payload_breakdown") if isinstance(batch_row.get("payload_breakdown"), dict) else {}
    per_sym = pb.get("per_symbol") if isinstance(pb.get("per_symbol"), dict) else {}

    drift_debug = batch_row.get("drift_debug") if isinstance(batch_row.get("drift_debug"), dict) else {}
    drifts = drift_debug.get("drifts") if isinstance(drift_debug.get("drifts"), dict) else {}
    payload_drift = drifts.get("payload_tokens_per_symbol") if isinstance(drifts.get("payload_tokens_per_symbol"), dict) else {}
    latency_drift = drifts.get("elapsed_ms") if isinstance(drifts.get("elapsed_ms"), dict) else {}

    contributors: list[dict[str, Any]] = []
    baselines = symbol_baselines or {}

    for sym in symbols:
        sym_u = str(sym).upper()
        sections = per_sym.get(sym_u) or per_sym.get(sym) or {}
        if not isinstance(sections, dict):
            continue
        base = baselines.get(sym_u) or {}
        for sec, chars in sections.items():
            stat = base.get(sec) if isinstance(base.get(sec), dict) else None
            if not stat:
                continue
            med = stat.get("median")
            if med is None:
                continue
            med_f = float(med)
            if med_f <= 0:
                continue
            cur = float(chars)
            delta_pct = (cur - med_f) / med_f * 100
            if abs(delta_pct) < 5:
                continue
            contributors.append(
                {
                    "symbol": sym_u,
                    "section": str(sec),
                    "current_chars": int(cur),
                    "baseline_median": round(med_f, 1),
                    "baseline_n": int(stat.get("n", 0) or 0),
                    "delta_pct": round(delta_pct, 1),
                    "delta_chars": int(round(cur - med_f)),
                }
            )

    contributors.sort(key=lambda row: abs(float(row.get("delta_pct", 0) or 0)), reverse=True)

    reasons: list[dict[str, str]] = []
    if symbol_count and symbol_count < batch_size:
        reasons.append(
            {
                "code": "tail_batch",
                "message": f"Tail batch ({symbol_count} symbols; full batch is {batch_size})",
            }
        )
    if attempts > 1:
        reasons.append(
            {
                "code": "retries",
                "message": f"{attempts} attempts — latency/cost may include retries",
            }
        )

    symbols_without_baseline = [
        str(s).upper()
        for s in symbols
        if str(s).upper() not in baselines
    ]
    for sym_u in symbols_without_baseline:
        reasons.append(
            {
                "code": "new_symbol",
                "message": f"{sym_u}: no per-symbol section baseline yet",
            }
        )

    payload_delta = payload_drift.get("delta_pct")
    latency_delta = latency_drift.get("delta_pct")
    if payload_delta is not None and latency_delta is not None:
        if abs(float(payload_delta)) < 10 and abs(float(latency_delta)) >= 20:
            reasons.append(
                {
                    "code": "latency_only",
                    "message": "Latency drift without payload drift — likely API/network slowness",
                }
            )
        if abs(float(payload_delta)) >= 15 and abs(float(latency_delta)) < 10:
            reasons.append(
                {
                    "code": "payload_only",
                    "message": "Payload grew but latency stable — dossier content change, not API delay",
                }
            )

    if batch_row.get("status") in ("failed", "degraded"):
        reasons.append(
            {
                "code": "batch_status",
                "message": f"Batch status {batch_row.get('status')} — scores may be degraded/skipped",
            }
        )

    winners_note = False
    for sym in symbols:
        sym_u = str(sym).upper()
        sec = (per_sym.get(sym_u) or {}).get("winners_note")
        if sec:
            winners_note = True
            break
    if winners_note:
        reasons.append(
            {
                "code": "winners_proxy",
                "message": "Winners momentum-proxy note added to payload for one or more symbols",
            }
        )

    summary = ""
    if contributors:
        top = contributors[0]
        sign = "+" if float(top["delta_pct"]) > 0 else ""
        summary = (
            f"Payload drift likely driven by {top['symbol']} {top['section']} "
            f"({sign}{top['delta_pct']}% vs per-symbol baseline)"
        )
    elif payload_delta is not None:
        summary = f"Batch-level payload drift {payload_delta}% — no per-section contributors above 5%"

    baseline_runs = drift_debug.get("baseline_sample_size")
    if baseline_runs is not None and int(baseline_runs) < 5:
        reasons.append(
            {
                "code": "thin_baseline",
                "message": f"Only {baseline_runs} prior run(s) with LLM data — drift may be noisy",
            }
        )

    per_symbol_summary: list[dict[str, Any]] = []
    for sym in symbols:
        sym_u = str(sym).upper()
        sym_contribs = [c for c in contributors if c["symbol"] == sym_u]
        if not sym_contribs:
            per_symbol_summary.append({"symbol": sym_u, "status": "stable"})
            continue
        top = sym_contribs[0]
        per_symbol_summary.append(
            {
                "symbol": sym_u,
                "status": "drift",
                "top_section": top["section"],
                "top_delta_pct": top["delta_pct"],
                "sections": sym_contribs[:5],
            }
        )

    return {
        "summary": summary,
        "contributors": contributors[:15],
        "reasons": reasons,
        "per_symbol": per_symbol_summary,
        "baseline_symbols": len(baselines),
    }


def stamp_llm_run_context(
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
    run_date: str | None = None,
    run_started_at: str | None = None,
) -> dict[str, Any]:
    """Attach health-run metadata to each batch row before persisting."""
    out = dict(payload)
    if run_id:
        out["run_id"] = run_id
    if run_date:
        out["run_date"] = run_date
    if run_started_at:
        out["run_started_at"] = run_started_at

    batches = []
    for batch in out.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        row = dict(batch)
        if run_id:
            row["run_id"] = run_id
        if run_date:
            row["run_date"] = run_date
        if run_started_at and not row.get("run_started_at"):
            row["run_started_at"] = run_started_at
        if not row.get("scored_at") and run_started_at:
            row["scored_at"] = run_started_at
        batches.append(row)
    out["batches"] = batches
    return out


def extract_usage(response: Any) -> dict[str, int]:
    """Normalize Gemini usage_metadata to a stable dict."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return empty_tokens()
    return {
        "prompt_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
        "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
        "cached_tokens": int(getattr(usage, "cached_content_token_count", 0) or 0),
        "thoughts_tokens": int(getattr(usage, "thoughts_token_count", 0) or 0),
        "total_tokens": int(getattr(usage, "total_token_count", 0) or 0),
    }


def add_tokens(target: dict[str, int], source: dict[str, int]) -> None:
    for key in _TOKEN_KEYS:
        target[key] = target.get(key, 0) + int(source.get(key, 0) or 0)


def format_token_summary(tokens: dict[str, int]) -> str:
    total = int(tokens.get("total_tokens", 0) or 0)
    prompt = int(tokens.get("prompt_tokens", 0) or 0)
    output = int(tokens.get("output_tokens", 0) or 0)
    return f"total={total:,} prompt={prompt:,} output={output:,}"


@dataclass
class LlmUsageCollector:
    """Accumulates batch-scoring LLM usage for one morning-ingestion run."""

    phase: str = "daily_ingestion"
    model: str = ""
    batches: list[dict[str, Any]] = field(default_factory=list)
    by_strategy: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record_batch(
        self,
        *,
        strategy: str,
        batch_label: str,
        symbols: list[str],
        tokens: dict[str, int],
        elapsed_ms: int,
        attempts: int,
        status: str,
        payload_chars: int,
        system_prompt_chars: int = 0,
        scored_at: str | None = None,
        payload_breakdown: dict[str, Any] | None = None,
        llm_io: dict[str, Any] | None = None,
    ) -> None:
        scored_at = scored_at or datetime.now(timezone.utc).isoformat()
        record = {
            "strategy": strategy,
            "batch": batch_label,
            "symbols": symbols,
            "status": status,
            "attempts": attempts,
            "payload_chars": payload_chars,
            "payload_tokens": estimate_tokens_from_chars(payload_chars),
            "payload_breakdown": payload_breakdown or {},
            "llm_io": llm_io or {},
            "system_prompt_chars": system_prompt_chars,
            "scored_at": scored_at,
            "elapsed_ms": elapsed_ms,
            **tokens,
        }
        self.batches.append(record)

        strat = self.by_strategy.setdefault(
            strategy,
            {
                "calls": 0,
                "retries": 0,
                "elapsed_ms": 0,
                **empty_tokens(),
            },
        )
        strat["calls"] = int(strat.get("calls", 0)) + 1
        strat["retries"] = int(strat.get("retries", 0)) + max(0, attempts - 1)
        strat["elapsed_ms"] = int(strat.get("elapsed_ms", 0)) + elapsed_ms
        add_tokens(strat, tokens)

        cost = estimate_cost_from_token_block(self.model, tokens)
        record_enriched = _enrich_batch_row(
            {**record, "estimated_cost_usd": cost, "model": self.model}
        )

        log.info(
            "[LLM USAGE] %s %s %s — %s est=$%.4f elapsed=%dms attempts=%d symbols=%s",
            self.phase,
            strategy,
            batch_label,
            format_token_summary(tokens),
            cost,
            elapsed_ms,
            attempts,
            ",".join(symbols),
        )
        log.info(
            "llm_call %s",
            json.dumps(
                {
                    "event": "llm_call",
                    "phase": self.phase,
                    "strategy": strategy,
                    "batch": batch_label,
                    "model": self.model,
                    "symbols": symbols,
                    "symbol_count": record_enriched.get("symbol_count"),
                    "prompt_tokens": tokens.get("prompt_tokens"),
                    "output_tokens": tokens.get("output_tokens"),
                    "total_tokens": tokens.get("total_tokens"),
                    "estimated_cost_usd": round(cost, 6),
                    "cost_per_symbol_usd": record_enriched.get("cost_per_symbol_usd"),
                    "tokens_per_symbol": record_enriched.get("tokens_per_symbol"),
                    "prompt_tokens_per_symbol": record_enriched.get("prompt_tokens_per_symbol"),
                    "output_tokens_per_symbol": record_enriched.get("output_tokens_per_symbol"),
                    "payload_chars": payload_chars,
                    "payload_tokens": record_enriched.get("payload_tokens"),
                    "payload_chars_per_symbol": record_enriched.get("payload_chars_per_symbol"),
                    "payload_tokens_per_symbol": record_enriched.get("payload_tokens_per_symbol"),
                    "payload_breakdown": record_enriched.get("payload_breakdown"),
                    "scored_at": scored_at,
                    "system_prompt_chars": record_enriched.get("system_prompt_chars"),
                    "system_prompt_estimated_tokens": record_enriched.get(
                        "system_prompt_estimated_tokens"
                    ),
                    "system_prompt_estimated_cost_usd": record_enriched.get(
                        "system_prompt_estimated_cost_usd"
                    ),
                    "elapsed_ms": elapsed_ms,
                    "attempts": attempts,
                    "status": status,
                },
                default=str,
            ),
        )
        if attempts > 1:
            log.warning(
                "[LLM USAGE] retry strategy=%s batch=%s attempts=%d status=%s",
                strategy,
                batch_label,
                attempts,
                status,
            )

    def merge_strategy_meta(
        self,
        strategy: str,
        *,
        candidates_scored: int,
        survivors: int,
        status: str,
    ) -> None:
        strat = self.by_strategy.setdefault(strategy, {"calls": 0, "retries": 0, **empty_tokens()})
        strat["candidates_scored"] = candidates_scored
        strat["survivors"] = survivors
        strat["status"] = status

    def to_dict(self) -> dict[str, Any]:
        totals: dict[str, Any] = {
            "calls": len(self.batches),
            "retries": sum(max(0, int(b.get("attempts", 1)) - 1) for b in self.batches),
            "elapsed_ms": sum(int(b.get("elapsed_ms", 0) or 0) for b in self.batches),
            **empty_tokens(),
        }
        for batch in self.batches:
            add_tokens(totals, batch)

        payload = {
            "phase": self.phase,
            "model": self.model,
            "totals": totals,
            "by_strategy": self.by_strategy,
            "batches": self.batches,
            "pricing_version": PRICING_VERSION,
        }
        return attach_cost_estimates(payload)

    def log_run_summary(self) -> dict[str, Any]:
        """Emit structured run-level summary; returns enriched payload."""
        payload = self.to_dict()
        log.info(
            "llm_run_summary %s",
            json.dumps(
                {
                    "event": "llm_run_summary",
                    "phase": self.phase,
                    "model": self.model,
                    "pricing_version": PRICING_VERSION,
                    "totals": payload.get("totals"),
                    "by_strategy": {
                        k: {
                            "estimated_cost_usd": v.get("estimated_cost_usd"),
                            "avg_cost_per_batch_usd": v.get("avg_cost_per_batch_usd"),
                            "cost_per_symbol_usd": v.get("cost_per_symbol_usd"),
                            "tokens_per_symbol": v.get("tokens_per_symbol"),
                            "prompt_tokens_per_symbol": v.get("prompt_tokens_per_symbol"),
                            "output_tokens_per_symbol": v.get("output_tokens_per_symbol"),
                            "calls": v.get("calls"),
                            "candidates_scored": v.get("candidates_scored"),
                            "survivors": v.get("survivors"),
                        }
                        for k, v in (payload.get("by_strategy") or {}).items()
                        if isinstance(v, dict)
                    },
                },
                default=str,
            ),
        )
        return payload
