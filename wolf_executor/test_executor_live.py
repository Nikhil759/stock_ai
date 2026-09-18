"""Tests for live executor persistence (mocked Kite)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from wolf_executor import run_wolf_executor

GUARDRAILS = {
    "stop_loss_pct": 15,
    "max_daily_loss_pct": 5,
    "max_capital_deployed_pct": 100,
    "max_per_stock_pct": 40,
    "min_trade_value": 1000,
}


class TestLiveExecutorPersist(unittest.TestCase):
    @patch("wolf_executor.executor._load_state")
    @patch("wolf_executor.executor._persist_buy")
    @patch("wolf_executor.executor.place_kite_order", return_value="order-123")
    @patch.dict(os.environ, {"WOLF_LIVE_ORDERS_ENABLED": "1"})
    def test_live_buy_persists_with_order_id(
        self, mock_place, mock_persist, mock_load_state
    ):
        mock_load_state.return_value = (8200.0, {}, GUARDRAILS)
        out = run_wolf_executor(
            "WTEST01",
            "real",
            sells=[],
            buys=[
                {
                    "symbol": "INFY",
                    "quantity": 1,
                    "buy_price": 1800.0,
                    "target": 2000.0,
                    "stop_loss": 1500.0,
                }
            ],
            cash_available=10_000,
            holdings=[],
            guardrails=GUARDRAILS,
            dry_run=False,
        )
        self.assertEqual(len(out["actions_taken"]), 1)
        mock_place.assert_called_once()
        mock_persist.assert_called_once()
        _, kwargs = mock_persist.call_args
        self.assertEqual(kwargs["mode"], "live")
        self.assertEqual(kwargs["kite_order_id"], "order-123")

    @patch("wolf_executor.executor._persist_buy")
    @patch(
        "wolf_executor.executor.place_kite_order",
        side_effect=RuntimeError("Kite order failed"),
    )
    @patch.dict(os.environ, {"WOLF_LIVE_ORDERS_ENABLED": "1"})
    def test_live_buy_failure_rejects_without_persist(self, _mock_place, mock_persist):
        out = run_wolf_executor(
            "WTEST01",
            "real",
            sells=[],
            buys=[{"symbol": "INFY", "quantity": 1, "buy_price": 1800.0}],
            cash_available=10_000,
            holdings=[],
            guardrails=GUARDRAILS,
            dry_run=False,
        )
        self.assertEqual(out["actions_taken"], [])
        self.assertEqual(len(out["actions_rejected"]), 1)
        mock_persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
