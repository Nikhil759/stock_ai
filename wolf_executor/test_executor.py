"""Offline unit tests for wolf_executor guardrails (no DB, no LLM)."""
from __future__ import annotations

import unittest

from wolf_executor import run_wolf_executor

GUARDRAILS = {
    "stop_loss_pct": 15,
    "max_daily_loss_pct": 5,
    "max_capital_deployed_pct": 100,
    "max_per_stock_pct": 40,
    "min_trade_value": 1000,
}


class TestWolfExecutor(unittest.TestCase):
    def _run(
        self,
        *,
        cash: float = 10_000,
        holdings: list | None = None,
        sells: list | None = None,
        buys: list | None = None,
        daily_loss_pct: float = 0,
        guardrails: dict | None = None,
    ) -> dict:
        return run_wolf_executor(
            "WTEST01",
            "paper",
            sells=sells or [],
            buys=buys or [],
            cash_available=cash,
            holdings=holdings or [],
            guardrails=guardrails or GUARDRAILS,
            dry_run=True,
            daily_loss_pct=daily_loss_pct,
        )

    def test_buy_happy_path(self):
        out = self._run(
            buys=[
                {
                    "symbol": "INFY",
                    "quantity": 2,
                    "buy_price": 1840.0,
                    "target": 2050.0,
                    "stop_loss": 1564.0,
                }
            ],
        )
        self.assertEqual(len(out["actions_taken"]), 1)
        self.assertEqual(out["actions_taken"][0]["action"], "BUY")
        self.assertEqual(out["cash_after"], 10_000 - 2 * 1840.0)
        self.assertEqual(out["actions_rejected"], [])

    def test_min_trade_value_rejects(self):
        out = self._run(
            buys=[{"symbol": "SMALL", "quantity": 1, "buy_price": 50.0}],
        )
        self.assertEqual(out["actions_taken"], [])
        self.assertEqual(len(out["actions_rejected"]), 1)
        self.assertEqual(
            out["guardrail_checks"]["min_trade_value"], "reject"
        )

    def test_cash_rejects_only_overflow_buy(self):
        loose = {**GUARDRAILS, "max_per_stock_pct": 100}
        out = self._run(
            cash=5_000,
            guardrails=loose,
            buys=[
                {
                    "symbol": "A",
                    "quantity": 2,
                    "buy_price": 2000.0,
                    "target": 2500,
                    "stop_loss": 1700,
                },
                {
                    "symbol": "B",
                    "quantity": 1,
                    "buy_price": 2000.0,
                    "target": 2500,
                    "stop_loss": 1700,
                },
            ],
        )
        self.assertEqual(len(out["actions_taken"]), 1)
        self.assertEqual(out["actions_taken"][0]["symbol"], "A")
        self.assertEqual(len(out["actions_rejected"]), 1)
        self.assertEqual(out["actions_rejected"][0]["symbol"], "B")

    def test_max_per_stock_rejects(self):
        out = self._run(
            cash=50_000,
            holdings=[
                {
                    "symbol": "BIG",
                    "quantity": 100,
                    "avg_buy_price": 200,
                    "current_price": 200,
                }
            ],
            buys=[
                {
                    "symbol": "BIG",
                    "quantity": 50,
                    "buy_price": 200.0,
                    "target": 250,
                    "stop_loss": 170,
                }
            ],
        )
        # portfolio ~70k; adding 10k to 20k position may exceed 40% cap
        self.assertEqual(out["actions_taken"], [])
        self.assertEqual(
            out["guardrail_checks"]["max_per_stock"], "reject"
        )

    def test_daily_loss_halts_buys_not_sells(self):
        out = self._run(
            cash=5_000,
            holdings=[
                {
                    "symbol": "TCS",
                    "quantity": 3,
                    "avg_buy_price": 3600,
                    "current_price": 3620.5,
                }
            ],
            sells=[{"symbol": "TCS", "quantity": 3, "reason": "exit"}],
            buys=[
                {
                    "symbol": "INFY",
                    "quantity": 1,
                    "buy_price": 1800.0,
                    "target": 2000,
                    "stop_loss": 1500,
                }
            ],
            daily_loss_pct=6.0,
        )
        self.assertEqual(len(out["actions_taken"]), 1)
        self.assertEqual(out["actions_taken"][0]["action"], "SELL")
        self.assertEqual(len(out["actions_rejected"]), 1)
        self.assertEqual(
            out["guardrail_checks"]["max_daily_loss"], "reject"
        )

    def test_sell_unheld_rejected(self):
        out = self._run(sells=[{"symbol": "FAKE", "quantity": 1}])
        self.assertEqual(out["actions_taken"], [])
        self.assertEqual(len(out["actions_rejected"]), 1)

    def test_summary_has_explicit_numbers(self):
        loose = {**GUARDRAILS, "max_per_stock_pct": 100}
        out = self._run(
            cash=6_000,
            guardrails=loose,
            buys=[
                {
                    "symbol": "INFY",
                    "quantity": 3,
                    "buy_price": 1838.2,
                    "target": 2050,
                    "stop_loss": 1564,
                }
            ],
        )
        s = out["summary"]
        self.assertIn("INFY", s)
        self.assertIn("₹", s)
        self.assertIn("6,000.00", s)


if __name__ == "__main__":
    unittest.main()
