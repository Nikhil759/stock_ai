"""Tests for batch scoring context-cache instruction sizing."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from selector.llm.client import (
    BATCH_CACHE_MIN_EST_TOKENS,
    batch_scoring_cache_instruction,
    batch_scoring_system_base,
    estimate_batch_cache_tokens,
    load_batch_cache_pad,
)


def test_batch_cache_pad_is_non_empty():
    assert len(load_batch_cache_pad()) > 500


def test_cache_instruction_includes_pad_but_inline_base_does_not():
    mc = {"nifty": {"close": 24500}, "vix": 12.5}
    cached = batch_scoring_cache_instruction("dip", mc)
    inline = batch_scoring_system_base("dip")
    pad = load_batch_cache_pad()
    assert pad in cached
    assert pad not in inline


def test_all_strategies_exceed_cache_min_tokens():
    mc = {"nifty": {"close": 24500}, "vix": 12.5, "date": "2026-08-30"}
    for strategy in ("value", "winners", "box", "dip"):
        text = batch_scoring_cache_instruction(strategy, mc)
        est = estimate_batch_cache_tokens(text)
        assert est >= BATCH_CACHE_MIN_EST_TOKENS, (
            f"{strategy}: est {est} < min {BATCH_CACHE_MIN_EST_TOKENS}"
        )
