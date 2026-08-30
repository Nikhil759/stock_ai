"""Offline Phase D checks — no live Gemini calls."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from scoring.batch_scorer import (
    BatchScoreResponse,
    _build_batch_payload,
    _validate_batch,
    _winners_proxy_note,
    run_batch_scoring,
)
from scoring.payload_trim import extract_shared_market_context
from selector.llm.usage import LlmUsageCollector, empty_tokens
from cache.shortlist_cache import save_shortlist, load_shortlist, shortlist_path


def test_winners_proxy_flag():
    note = _winners_proxy_note(
        {
            "earnings_growth_yoy": None,
            "earnings_proxy": "return_3m>0 (YoY earnings growth not on dossier)",
            "return_3m": 12.3,
        }
    )
    assert note and "momentum" in note.lower()
    payload = json.loads(
        _build_batch_payload(
            "winners",
            [
                {
                    "symbol": "ABB",
                    "funnel_reasons": {
                        "earnings_proxy": "return_3m>0 (YoY earnings growth not on dossier)",
                        "return_3m": 0.5,
                    },
                    "dossier": {"fundamentals": {"price": 100}},
                }
            ],
            include_market_context=False,
        )
    )
    assert payload["stocks"][0]["note"].startswith("earnings growth data unavailable")


def _sample_mc_dossier(symbol: str = "ITC") -> dict:
    return {
        "symbol": symbol,
        "funnel_reasons": {"pe": 12},
        "dossier": {
            "meta": {"symbol": symbol, "sector": "FMCG"},
            "fundamentals": {"price": 100},
            "market_context": {
                "nifty_above_200dma": True,
                "nifty_trend": "up",
                "india_vix": 13.0,
                "vix_regime": "calm",
                "market_breadth_pct_above_200dma": 40.0,
                "sector": "FMCG",
            },
            "news": {
                "aggregate_sentiment": "neutral",
                "items": [{"headline": "Test", "summary": "Short"}],
            },
        },
    }


def test_batch_payload_dedupes_market_context():
    batch = [_sample_mc_dossier("ITC"), _sample_mc_dossier("HUL")]
    shared = extract_shared_market_context(batch[0]["dossier"])
    payload = json.loads(
        _build_batch_payload(
            "value",
            batch,
            include_market_context=True,
            shared_market_context=shared,
        )
    )
    assert "market_context" in payload
    assert payload["market_context"]["nifty_trend"] == "up"
    assert "market_context" not in payload["stocks"][0]["dossier"]
    assert payload["stocks"][0]["sector"]["sector"] == "FMCG"
    assert "market_context" not in payload["stocks"][1]["dossier"]


def test_batch_payload_compact_and_smaller_with_dedup():
    batch = [_sample_mc_dossier(f"S{i}") for i in range(3)]
    shared = extract_shared_market_context(batch[0]["dossier"])
    compact = _build_batch_payload(
        "value",
        batch,
        include_market_context=True,
        shared_market_context=shared,
    )
    assert "\n  " not in compact
    assert len(compact) < len(
        json.dumps(
            json.loads(compact),
            indent=2,
            default=str,
        )
    )


def test_validate_absolute_merit_batch():
    raw = {
        "scores": [
            {"symbol": "A", "conviction": 82, "verdict": "buy", "reasoning": "strong"},
            {"symbol": "B", "conviction": 75, "verdict": "buy", "reasoning": "also strong"},
            {"symbol": "C", "conviction": 30, "verdict": "skip", "reasoning": "weak"},
        ]
    }
    scores = _validate_batch(raw, ["A", "B", "C"])
    assert [s.verdict for s in scores] == ["buy", "buy", "skip"]


def test_shortlist_overwrite_and_frozen_price(tmp_path, monkeypatch=None):
    day = date(2026, 7, 14)
    # Point SHORTLIST_DIR at a temp dir via save path override
    from cache import shortlist_cache as sc

    original = sc.SHORTLIST_DIR
    sc.SHORTLIST_DIR = tmp_path
    try:
        c1 = [
            {
                "symbol": "ITC",
                "conviction": 78,
                "verdict": "buy",
                "reasoning": "cheap",
                "price": 450.0,
            }
        ]
        p1 = save_shortlist("value", day, c1)
        assert p1.name == "shortlist_value_2026-07-14.json"
        loaded = load_shortlist("value", day)
        assert loaded[0]["price"] == 450.0
        assert loaded[0]["date"] == "2026-07-14"

        c2 = [
            {
                "symbol": "TCS",
                "conviction": 60,
                "verdict": "watch",
                "reasoning": "ok",
                "price": 3800.0,
            }
        ]
        save_shortlist("value", day, c2)
        loaded2 = load_shortlist("value", day)
        assert len(loaded2) == 1
        assert loaded2[0]["symbol"] == "TCS"
    finally:
        sc.SHORTLIST_DIR = original


def test_batch_scoring_mocked_parse_retry():
    candidates = [
        {
            "symbol": "X",
            "funnel_reasons": {"pe": 10},
            "dossier": {"fundamentals": {"price": 123.45}},
        },
        {
            "symbol": "Y",
            "funnel_reasons": {"pe": 12},
            "dossier": {"fundamentals": {"price": 99.0}},
        },
    ]
    bad = "not json"
    good = BatchScoreResponse(
        scores=[
            {"symbol": "X", "conviction": 70, "verdict": "buy", "reasoning": "ok x"},
            {"symbol": "Y", "conviction": 55, "verdict": "watch", "reasoning": "ok y"},
        ]
    ).model_dump_json()

    calls = {"n": 0}

    def fake_call(strategy, payload, *, cache_name=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return bad, empty_tokens(), 0.05
        return good, {
            "prompt_tokens": 500,
            "output_tokens": 80,
            "cached_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 580,
        }, 0.12

    collector = LlmUsageCollector(model="gemini-2.5-flash")
    with patch("scoring.batch_scorer.get_or_create_batch_scoring_cache", return_value=None):
        with patch("scoring.batch_scorer._call_gemini", side_effect=fake_call):
            with patch("scoring.batch_scorer.BATCH_PAUSE_SEC", 0):
                result = run_batch_scoring(
                    "value",
                    candidates,
                    as_of=date(2026, 7, 14),
                    usage_collector=collector,
                )

    assert calls["n"] == 2  # one retry after parse failure
    assert len(result.survivors) == 2
    assert {s["symbol"] for s in result.survivors} == {"X", "Y"}
    assert result.survivors[0]["price"] == 123.45
    assert result.usage["total_tokens"] == 580
    usage_payload = collector.to_dict()
    assert usage_payload["totals"]["calls"] == 1
    assert usage_payload["totals"]["retries"] == 1
    # Failed parse attempt still billed if the API returned usage on retry path
    assert usage_payload["totals"]["total_tokens"] == 580


if __name__ == "__main__":
    test_winners_proxy_flag()
    test_batch_payload_dedupes_market_context()
    test_batch_payload_compact_and_smaller_with_dedup()
    test_validate_absolute_merit_batch()
    test_shortlist_overwrite_and_frozen_price(Path("/tmp/phase_d_shortlist_test"))
    test_batch_scoring_mocked_parse_retry()
    print("OK — offline Phase D checks passed")
