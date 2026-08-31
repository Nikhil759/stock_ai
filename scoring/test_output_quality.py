"""Tests for batch scoring output quality checks."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from scoring.batch_scorer import BatchStockScore
from scoring.output_quality import (
    build_quality_summary,
    check_directional_claims,
    check_metric_citations,
    check_strategy_rules,
    check_verdict_bands,
    evaluate_batch_output,
)


def test_verdict_band_mismatch():
    flags = check_verdict_bands(85, "skip")
    assert any(f["code"] == "verdict_band_mismatch" for f in flags)


def test_dip_below_200dma_buy_flagged():
    flags = check_strategy_rules(
        "dip",
        conviction=70,
        verdict="buy",
        dossier={"technicals": {"above_200dma": False, "rsi_2": 12.0}},
        funnel_reasons={},
    )
    assert any(f["code"] == "dip_below_200dma" for f in flags)


def test_grounding_rsi_match():
    flags, summary = check_metric_citations(
        "RSI(2) of 8.1 indicates oversold conditions.",
        {"technicals": {"rsi_2": 8.1, "above_200dma": True}},
    )
    assert summary["metrics_matched"] >= 1
    assert not any(f["code"] == "grounding_metric_mismatch" for f in flags)


def test_grounding_rsi_mismatch():
    flags, _ = check_metric_citations(
        "RSI(2) of 18.5 is elevated.",
        {"technicals": {"rsi_2": 8.1}},
    )
    assert any(f["code"] == "grounding_metric_mismatch" for f in flags)


def test_directional_dma_contradiction():
    flags = check_directional_claims(
        "Price remains above its 200-day moving average.",
        {"technicals": {"above_200dma": False}},
    )
    assert any(f["code"] == "grounding_dma_contradiction" for f in flags)


def test_batch_all_buy_outlier():
    batch = [
        {"symbol": "A", "dossier": {"technicals": {"rsi_2": 10}}, "funnel_reasons": {}},
        {"symbol": "B", "dossier": {"technicals": {"rsi_2": 11}}, "funnel_reasons": {}},
        {"symbol": "C", "dossier": {"technicals": {"rsi_2": 9}}, "funnel_reasons": {}},
        {"symbol": "D", "dossier": {"technicals": {"rsi_2": 12}}, "funnel_reasons": {}},
    ]
    scores = [
        BatchStockScore(symbol=s["symbol"], conviction=75, verdict="buy", reasoning="ok")
        for s in batch
    ]
    report = evaluate_batch_output("dip", batch, scores)
    assert any(f["code"] == "batch_all_buy" for f in report["batch_flags"])


def test_build_quality_summary_rollup():
    batches = [
        {
            "strategy": "dip",
            "symbol_count": 2,
            "run_id": "run-1",
            "symbols": ["A", "B"],
            "output_quality": {
                "flag_count": 1,
                "symbols_flagged": 1,
                "severity": "warn",
                "batch_flags": [],
                "outliers": [],
                "stocks": [
                    {
                        "symbol": "A",
                        "flag_count": 0,
                        "flags": [],
                        "grounding": {"metrics_cited": 1, "metrics_matched": 1},
                    },
                    {
                        "symbol": "B",
                        "flag_count": 1,
                        "flags": [{"code": "grounding_metric_mismatch", "severity": "warn", "message": "x"}],
                        "grounding": {"metrics_cited": 1, "metrics_matched": 0},
                    },
                ],
            },
        },
        {
            "strategy": "value",
            "symbol_count": 2,
            "run_id": "run-2",
            "symbols": ["C", "D"],
            "output_quality": {
                "flag_count": 0,
                "symbols_flagged": 0,
                "severity": "ok",
                "batch_flags": [],
                "outliers": [],
                "stocks": [
                    {"symbol": "C", "flag_count": 0, "flags": [], "grounding": {"metrics_cited": 0, "metrics_matched": 0}},
                    {"symbol": "D", "flag_count": 0, "flags": [], "grounding": {"metrics_cited": 0, "metrics_matched": 0}},
                ],
            },
        },
    ]
    summary = build_quality_summary(batches)
    assert summary["has_data"] is True
    assert summary["run_count"] == 2
    assert summary["unique_symbols"] == 4
    assert summary["total_symbols"] == 4
    assert summary["symbols_clean"] == 3
    assert summary["clean_pct"] == 75.0
    assert summary["by_category"]["facts"] == 1
    assert summary["grounding"]["metrics_cited"] == 2
    assert summary["grounding"]["metrics_matched"] == 1
    assert summary["grounding"]["match_pct"] == 50.0
    assert summary["by_strategy"]["dip"]["clean_pct"] == 50.0
    assert summary["by_strategy"]["dip"]["grounding"]["match_pct"] == 50.0


if __name__ == "__main__":
    test_verdict_band_mismatch()
    test_dip_below_200dma_buy_flagged()
    test_grounding_rsi_match()
    test_grounding_rsi_mismatch()
    test_directional_dma_contradiction()
    test_batch_all_buy_outlier()
    test_build_quality_summary_rollup()
    print("OK — output quality tests passed")
