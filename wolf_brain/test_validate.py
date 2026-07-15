#!/usr/bin/env python3
"""Offline validation checks for Wolf Brain (no Gemini required).

Usage:
    PYTHONPATH=. python -m wolf_brain.test_validate
"""
from __future__ import annotations

from wolf_brain.schemas import BrainPick, DailyReviewBrainOutput, DeployBrainOutput
from wolf_brain.validate import (
    normalize_guardrails,
    validate_daily_review_output,
    validate_deploy_output,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_min_trade_value() -> None:
    g = normalize_guardrails({"min_trade_value": 2000})
    raw = DeployBrainOutput(
        birth_intent="test",
        picks=[
            BrainPick(
                symbol="ITC",
                quantity=1,
                buy_price=100,
                target=120,
                stop_loss=85,
                conviction=50,
                reasoning="too small",
            ),
        ],
    )
    out = validate_deploy_output(
        raw,
        cash_available=10000,
        guardrails=g,
        shortlist_symbols={"ITC"},
    )
    _assert(len(out.picks) == 0, "expected min trade drop")
    print("  ok min_trade_value")


def test_trim_over_cash() -> None:
    g = normalize_guardrails({"min_trade_value": 1000})
    raw = DeployBrainOutput(
        birth_intent="test",
        picks=[
            BrainPick(
                symbol="ITC",
                quantity=10,
                buy_price=400,
                target=480,
                stop_loss=340,
                conviction=80,
                reasoning="a",
            ),
            BrainPick(
                symbol="INFY",
                quantity=5,
                buy_price=1800,
                target=2000,
                stop_loss=1500,
                conviction=70,
                reasoning="b",
            ),
        ],
    )
    out = validate_deploy_output(
        raw,
        cash_available=5000,
        guardrails=g,
        shortlist_symbols={"ITC", "INFY"},
    )
    total = sum(p.quantity * p.buy_price for p in out.picks)
    _assert(total <= 5000, f"expected trim under cash, got {total}")
    _assert(len(out.picks) >= 1, "expected at least one pick")
    print("  ok trim_over_cash")


def test_inverted_target_stop() -> None:
    g = normalize_guardrails({"stop_loss_pct": 15, "min_trade_value": 1000})
    raw = DeployBrainOutput(
        birth_intent="test",
        picks=[
            BrainPick(
                symbol="ITC",
                quantity=10,
                buy_price=400,
                target=380,
                stop_loss=450,
                conviction=80,
                reasoning="bad geometry",
            ),
            BrainPick(
                symbol="INFY",
                quantity=2,
                buy_price=1800,
                target=2000,
                stop_loss=1500,
                conviction=70,
                reasoning="good geometry",
            ),
        ],
    )
    out = validate_deploy_output(
        raw,
        cash_available=10000,
        guardrails=g,
        shortlist_symbols={"ITC", "INFY"},
    )
    syms = {p.symbol for p in out.picks}
    _assert("ITC" not in syms, "expected inverted pick dropped")
    _assert("INFY" in syms, "expected valid pick kept")
    print("  ok inverted_target_stop")


def test_daily_default_hold() -> None:
    g = normalize_guardrails({})
    raw = DailyReviewBrainOutput(
        holdings_review=[],
        new_picks=[],
        current_intent="hold",
        daily_update="quiet day",
    )
    out = validate_daily_review_output(
        raw,
        cash_available=1000,
        guardrails=g,
        shortlist_symbols=set(),
        held_symbols={"TCS"},
    )
    _assert(len(out.holdings_review) == 1, "expected default hold for TCS")
    _assert(out.holdings_review[0].verdict == "hold", "expected hold verdict")
    print("  ok daily_default_hold")


def main() -> None:
    print("[WOLF BRAIN] validate tests")
    test_trim_over_cash()
    test_min_trade_value()
    test_inverted_target_stop()
    test_daily_default_hold()
    print("all passed")


if __name__ == "__main__":
    main()
