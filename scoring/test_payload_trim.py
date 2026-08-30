"""Tests for LLM payload trimming helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from scoring.payload_trim import (
    extract_shared_market_context,
    extract_stock_sector_context,
    slim_dossier_for_llm,
    trim_news_for_llm,
)


def _sample_dossier() -> dict:
    return {
        "meta": {"symbol": "ITC", "sector": "FMCG"},
        "fundamentals": {"price": 450.0},
        "market_context": {
            "nifty_above_200dma": True,
            "nifty_trend": "up",
            "india_vix": 13.2,
            "vix_regime": "calm",
            "market_breadth_pct_above_200dma": 42.0,
            "sector": "FMCG",
        },
        "news": {
            "aggregate_sentiment": "positive",
            "sentiment_vs_price": "aligned",
            "items": [
                {
                    "date": "2026-08-30",
                    "headline": "Headline one",
                    "summary": "x" * 200,
                    "sentiment_score": 0.2,
                    "match_score": 88,
                    "source": "Reuters",
                    "event_type": "general",
                },
                {
                    "date": "2026-08-29",
                    "headline": "Headline two",
                    "summary": "y" * 200,
                    "sentiment_score": -0.1,
                    "match_score": 75,
                    "source": "Mint",
                    "event_type": "general",
                },
            ],
        },
    }


def test_extract_shared_market_context():
    mc = extract_shared_market_context(_sample_dossier())
    assert mc["nifty_trend"] == "up"
    assert "sector" not in mc


def test_extract_stock_sector_context():
    sector = extract_stock_sector_context(_sample_dossier())
    assert sector["sector"] == "FMCG"


def test_trim_news_for_llm():
    trimmed = trim_news_for_llm(_sample_dossier()["news"])
    assert trimmed["aggregate_sentiment"] == "positive"
    assert len(trimmed["items"]) <= 3
    assert "match_score" not in trimmed["items"][0]
    assert "source" not in trimmed["items"][0]
    assert len(trimmed["items"][0]["summary"]) <= 121


def test_slim_dossier_omits_market_context():
    slim = slim_dossier_for_llm(_sample_dossier())
    assert "market_context" not in slim
    assert "news" in slim
    assert len(json.dumps(slim)) < len(json.dumps(_sample_dossier()))


if __name__ == "__main__":
    test_extract_shared_market_context()
    test_extract_stock_sector_context()
    test_trim_news_for_llm()
    test_slim_dossier_omits_market_context()
    print("OK — payload trim tests passed")
