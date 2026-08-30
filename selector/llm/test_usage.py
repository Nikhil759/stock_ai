"""Tests for LLM usage extraction and rollups."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from selector.llm.usage import (
    LlmUsageCollector,
    aggregate_llm_batches,
    analyze_batch_drift,
    build_llm_baselines,
    build_llm_drift_report,
    compare_to_baseline,
    enrich_derived_metrics,
    estimate_cost_usd,
    extract_usage,
    finalize_llm_usage_payload,
    stamp_llm_run_context,
)


def test_extract_usage_from_response():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=1200,
            candidates_token_count=180,
            cached_content_token_count=0,
            thoughts_token_count=12,
            total_token_count=1392,
        )
    )
    usage = extract_usage(response)
    assert usage == {
        "prompt_tokens": 1200,
        "output_tokens": 180,
        "cached_tokens": 0,
        "thoughts_tokens": 12,
        "total_tokens": 1392,
    }


def test_collector_rollups():
    collector = LlmUsageCollector(phase="daily_ingestion", model="gemini-2.5-flash")
    tokens = {
        "prompt_tokens": 1000,
        "output_tokens": 100,
        "cached_tokens": 0,
        "thoughts_tokens": 0,
        "total_tokens": 1100,
    }
    collector.record_batch(
        strategy="box",
        batch_label="1/2",
        symbols=["ITC"],
        tokens=tokens,
        elapsed_ms=900,
        attempts=2,
        status="ok",
        payload_chars=5000,
        system_prompt_chars=3200,
    )
    collector.merge_strategy_meta(
        "box",
        candidates_scored=8,
        survivors=3,
        status="success",
    )
    payload = collector.to_dict()
    assert payload["model"] == "gemini-2.5-flash"
    assert payload["totals"]["calls"] == 1
    assert payload["totals"]["retries"] == 1
    assert payload["totals"]["total_tokens"] == 1100
    assert payload["by_strategy"]["box"]["survivors"] == 3
    assert payload["batches"][0]["symbols"] == ["ITC"]
    assert payload["by_strategy"]["box"]["avg_cost_per_batch_usd"] == payload["totals"]["estimated_cost_usd"]
    assert payload["batches"][0]["symbol_count"] == 1
    assert payload["batches"][0]["cost_per_symbol_usd"] == payload["batches"][0]["estimated_cost_usd"]
    assert payload["batches"][0]["payload_chars_per_symbol"] == 5000
    assert payload["batches"][0]["payload_tokens_per_symbol"] == 1250
    assert payload["batches"][0]["system_prompt_estimated_tokens"] > 0
    assert payload["batches"][0]["scored_at"]


def test_enrich_derived_metrics_multi_symbol_batch():
    payload = enrich_derived_metrics(
        {
            "model": "gemini-2.5-flash",
            "totals": {"calls": 1, "prompt_tokens": 8000, "output_tokens": 400, "total_tokens": 8400},
            "by_strategy": {
                "box": {
                    "calls": 1,
                    "candidates_scored": 8,
                    "survivors": 3,
                    "prompt_tokens": 8000,
                    "output_tokens": 400,
                    "total_tokens": 8400,
                    "estimated_cost_usd": 0.0034,
                }
            },
            "batches": [
                {
                    "strategy": "box",
                    "batch": "1/1",
                    "symbols": ["A", "B", "C", "D", "E", "F", "G", "H"],
                    "payload_chars": 32000,
                    "system_prompt_chars": 4000,
                    "prompt_tokens": 8000,
                    "output_tokens": 400,
                    "total_tokens": 8400,
                    "estimated_cost_usd": 0.0034,
                    "elapsed_ms": 2000,
                    "attempts": 1,
                    "status": "ok",
                }
            ],
        }
    )
    batch = payload["batches"][0]
    assert batch["symbol_count"] == 8
    assert batch["payload_chars_per_symbol"] == 4000
    assert batch["payload_tokens_per_symbol"] == 1000
    assert batch["system_prompt_estimated_cost_usd"] > 0
    assert abs(batch["cost_per_symbol_usd"] - 0.0034 / 8) < 1e-9
    assert batch["prompt_tokens_per_symbol"] == 1000
    assert batch["output_tokens_per_symbol"] == 50
    strat = payload["by_strategy"]["box"]
    assert strat["avg_cost_per_batch_usd"] == 0.0034
    assert abs(strat["cost_per_symbol_usd"] - 0.0034 / 8) < 1e-9
    assert strat["prompt_tokens_per_symbol"] == 1000
    assert strat["output_tokens_per_symbol"] == 50
    assert strat["avg_prompt_tokens_per_batch"] == 8000
    assert strat["avg_output_tokens_per_batch"] == 400
    assert strat["cost_per_survivor_usd"] == 0.0034 / 3


def test_baselines_and_drift():
    runs = []
    for cost in (0.10, 0.12, 0.11, 0.13, 0.10):
        runs.append(
            {
                "stages": {
                    "llm_usage": {
                        "model": "gemini-2.5-flash",
                        "totals": {
                            "calls": 4,
                            "estimated_cost_usd": cost,
                            "total_tokens": 50000,
                            "prompt_tokens": 45000,
                            "output_tokens": 5000,
                        },
                        "by_strategy": {
                            "box": {
                                "calls": 2,
                                "estimated_cost_usd": cost / 2,
                                "avg_cost_per_batch_usd": cost / 4,
                                "cost_per_symbol_usd": cost / 40,
                                "total_tokens": 25000,
                                "prompt_tokens": 22000,
                                "output_tokens": 3000,
                            }
                        },
                        "batches": [
                            {
                                "strategy": "box",
                                "symbols": ["A", "B"],
                                "payload_chars": 8000,
                                "system_prompt_chars": 4000,
                                "cost_per_symbol_usd": cost / 40,
                                "tokens_per_symbol": 1000,
                                "elapsed_ms": 1500,
                                "total_tokens": 2000,
                                "prompt_tokens": 1800,
                                "output_tokens": 200,
                                "estimated_cost_usd": cost / 8,
                            }
                        ],
                    }
                }
            }
        )
    baselines = build_llm_baselines(runs, window=5)
    assert baselines["sample_size"] == 5
    med = baselines["totals"]["estimated_cost_usd"]["median"]
    drift = compare_to_baseline(0.25, baselines["totals"]["estimated_cost_usd"])
    assert drift["delta_pct"] is not None
    assert drift["severity"] in ("warn", "alert")

    snap = enrich_derived_metrics(
        {
            "model": "gemini-2.5-flash",
            "totals": {
                "calls": 1,
                "prompt_tokens": 16000,
                "output_tokens": 800,
                "thoughts_tokens": 0,
                "total_tokens": 16800,
                "estimated_cost_usd": 0.25,
            },
            "by_strategy": {
                "box": {
                    "calls": 1,
                    "prompt_tokens": 16000,
                    "output_tokens": 800,
                    "total_tokens": 16800,
                    "estimated_cost_usd": 0.25,
                }
            },
            "batches": [
                {
                    "strategy": "box",
                    "batch": "1/1",
                    "symbols": ["A", "B"],
                    "payload_chars": 20000,
                    "system_prompt_chars": 4000,
                    "prompt_tokens": 16000,
                    "output_tokens": 800,
                    "thoughts_tokens": 0,
                    "total_tokens": 16800,
                    "estimated_cost_usd": 0.25,
                    "elapsed_ms": 5000,
                    "attempts": 1,
                    "status": "ok",
                }
            ],
        }
    )
    report = build_llm_drift_report(snap, baselines)
    assert report["baseline_sample_size"] == 5
    assert "payload_tokens_per_symbol" in report["totals"]
    assert report["totals"]["payload_tokens_per_symbol"]["delta_pct"] is not None
    assert report["batches"][0]["drifts"]["elapsed_ms"]["delta_pct"] is not None


def test_estimate_cost_usd():
    cost = estimate_cost_usd(
        model="gemini-2.5-flash",
        prompt_tokens=1_000_000,
        output_tokens=0,
    )
    assert abs(cost - 0.30) < 0.001
    cost2 = estimate_cost_usd(
        model="gemini-2.5-flash",
        prompt_tokens=100_000,
        output_tokens=10_000,
        cached_tokens=20_000,
    )
    # 80k regular input @ $0.30/M + 20k cached @ $0.03/M + 10k output @ $2.50/M
    expected = (80_000 * 0.30 + 20_000 * 0.03 + 10_000 * 2.50) / 1_000_000
    assert abs(cost2 - expected) < 0.0001


def test_stamp_llm_run_context():
    payload = stamp_llm_run_context(
        {
            "batches": [
                {"strategy": "box", "batch": "1/1", "scored_at": "2026-08-30T10:00:00+00:00"},
            ]
        },
        run_id="abc-123",
        run_date="2026-08-30",
        run_started_at="2026-08-30T09:00:00+00:00",
    )
    assert payload["run_id"] == "abc-123"
    batch = payload["batches"][0]
    assert batch["run_id"] == "abc-123"
    assert batch["run_date"] == "2026-08-30"
    assert batch["scored_at"] == "2026-08-30T10:00:00+00:00"


def test_finalize_llm_usage_payload_attaches_drift_debug():
    payload = finalize_llm_usage_payload(
        {
            "model": "gemini-2.5-flash",
            "totals": {
                "calls": 1,
                "prompt_tokens": 8000,
                "output_tokens": 400,
                "total_tokens": 8400,
            },
            "by_strategy": {"box": {"calls": 1, "prompt_tokens": 8000, "output_tokens": 400, "total_tokens": 8400}},
            "batches": [
                {
                    "strategy": "box",
                    "batch": "1/1",
                    "symbols": ["A", "B"],
                    "payload_chars": 8000,
                    "payload_breakdown": {"sections": {"news": 5000, "fundamentals": 3000}},
                    "prompt_tokens": 8000,
                    "output_tokens": 400,
                    "total_tokens": 8400,
                    "elapsed_ms": 2000,
                    "attempts": 1,
                    "status": "ok",
                }
            ],
        },
        {"sample_size": 0},
    )
    batch = payload["batches"][0]
    assert "drift_debug" in batch
    assert batch["payload_breakdown"]["sections"]["news"] == 5000
    assert "drift_report" in payload


def test_aggregate_llm_batches_multi_run():
    batches = [
        {
            "strategy": "value",
            "batch": "1/2",
            "symbols": ["A"],
            "symbol_count": 1,
            "prompt_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "elapsed_ms": 1000,
            "attempts": 1,
            "payload_chars": 4000,
            "estimated_cost_usd": 0.01,
            "run_date": "2026-08-29",
        },
        {
            "strategy": "dip",
            "batch": "1/1",
            "symbols": ["B", "C"],
            "symbol_count": 2,
            "prompt_tokens": 2000,
            "output_tokens": 200,
            "total_tokens": 2200,
            "elapsed_ms": 2000,
            "attempts": 2,
            "payload_chars": 8000,
            "estimated_cost_usd": 0.02,
            "run_date": "2026-08-30",
        },
    ]
    agg = aggregate_llm_batches(batches, model="gemini-2.5-flash")
    assert agg["totals"]["calls"] == 2
    assert agg["totals"]["retries"] == 1
    assert agg["totals"]["total_tokens"] == 3300
    assert len(agg["strategies"]) == 2


def test_analyze_batch_drift_per_symbol():
    baselines = {
        "ITC": {"news": {"median": 2000.0, "n": 8}, "fundamentals": {"median": 800.0, "n": 8}},
        "HDFCBANK": {"news": {"median": 2500.0, "n": 6}},
    }
    batch_row = {
        "strategy": "value",
        "batch": "1/2",
        "symbols": ["ITC", "HDFCBANK"],
        "symbol_count": 2,
        "attempts": 1,
        "status": "ok",
        "payload_breakdown": {
            "per_symbol": {
                "ITC": {"news": 3200, "fundamentals": 810},
                "HDFCBANK": {"news": 4100},
            }
        },
        "drift_debug": {
            "drifts": {
                "payload_tokens_per_symbol": {"delta_pct": 42.0},
                "elapsed_ms": {"delta_pct": 5.0},
            },
            "baseline_sample_size": 8,
        },
    }
    analysis = analyze_batch_drift(batch_row, baselines, batch_size=8)
    assert analysis["contributors"]
    assert analysis["contributors"][0]["symbol"] in ("ITC", "HDFCBANK")
    assert analysis["contributors"][0]["delta_pct"] > 0
    assert any(r["code"] == "tail_batch" for r in analysis["reasons"])


if __name__ == "__main__":
    test_extract_usage_from_response()
    test_collector_rollups()
    test_enrich_derived_metrics_multi_symbol_batch()
    test_baselines_and_drift()
    test_estimate_cost_usd()
    test_stamp_llm_run_context()
    test_finalize_llm_usage_payload_attaches_drift_debug()
    test_analyze_batch_drift_per_symbol()
    print("OK — LLM usage tests passed")
