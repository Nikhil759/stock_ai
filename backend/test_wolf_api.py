"""Offline tests for wolf_api facade (no DB)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import wolf_api
from wolf_api import wolf_to_bot


class TestWolfApiFacade(unittest.TestCase):
    def test_wolf_to_bot_shape(self):
        wolf = {
            "wolf_id": "W0001",
            "wolf_name": "Alpha",
            "strategy_code": "VALUE",
            "budget_initial": 10000,
            "budget_available": 6400,
            "status": "active",
            "guardrails": {
                "stop_loss_pct": 15,
                "max_daily_loss_pct": 5,
                "max_capital_deployed_pct": 100,
                "max_per_stock_pct": 40,
            },
            "created_at": None,
            "closed_at": None,
            "circuit_breaker_tripped_at": None,
        }
        holdings = [
            {
                "symbol": "INFY",
                "quantity": 2,
                "avg_buy_price": 1800,
                "status": "open",
                "sell_target": 2000,
                "stop_loss": 1500,
            }
        ]
        with patch.object(wolf_api, "_fetch_ltps", return_value={"INFY": 1850.0}):
            bot = wolf_to_bot(wolf, holdings=holdings, ltps={"INFY": 1850.0})
        self.assertEqual(bot["id"], "W0001")
        self.assertEqual(bot["strategy"], "value")
        self.assertEqual(bot["status"], "running")
        self.assertEqual(bot["availableCash"], 6400.0)
        self.assertGreater(bot["portfolioValue"], 6400)


if __name__ == "__main__":
    unittest.main()
