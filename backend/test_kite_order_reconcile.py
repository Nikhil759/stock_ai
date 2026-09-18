"""Unit tests for live Kite order reconciliation."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / "backend"
for p in (_ROOT, _BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backend.kite_order_reconcile import reconcile_live_orders_for_wolf


class TestReconcileLiveOrders(unittest.TestCase):
    @patch("backend.kite_order_reconcile.repo")
    @patch("backend.kite_order_reconcile._get_kite_nonblocking")
    def test_fill_updates_trade_and_holding(self, mock_kite_fn, mock_repo):
        mock_repo.get_wolf.return_value = {"wolf_id": "W0015", "mode": "live"}
        mock_repo.list_reconcilable_live_buys.return_value = [
            {
                "trade_id": 42,
                "wolf_id": "W0015",
                "symbol": "LUPIN",
                "quantity": 10,
                "price": 2000.0,
                "order_status": "pending",
                "kite_order_id": "12345",
            }
        ]
        mock_repo.list_open_holdings.return_value = [
            {
                "symbol": "LUPIN",
                "quantity": 10,
                "sell_target": 2200.0,
                "stop_loss": 1800.0,
            }
        ]
        mock_repo.get_wolf.side_effect = [
            {"wolf_id": "W0015", "mode": "live", "budget_available": 50000.0},
        ]

        kite = MagicMock()
        kite.order_history.return_value = [
            {"status": "OPEN"},
            {"status": "COMPLETE", "filled_quantity": 10, "average_price": 1995.0},
        ]
        mock_kite_fn.return_value = kite

        result = reconcile_live_orders_for_wolf("W0015")

        self.assertEqual(len(result["filled"]), 1)
        self.assertEqual(result["filled"][0]["ticker"], "LUPIN")
        self.assertEqual(result["filled"][0]["quantity"], 10)
        mock_repo.update_trade_order.assert_called_once_with(
            42,
            order_status="complete",
            quantity=10,
            price=1995.0,
        )
        mock_repo.upsert_holding.assert_called_once()

    @patch("backend.kite_order_reconcile.repo")
    @patch("backend.kite_order_reconcile._get_kite_nonblocking")
    def test_rejected_refunds_and_closes_holding(self, mock_kite_fn, mock_repo):
        mock_repo.get_wolf.return_value = {
            "wolf_id": "W0015",
            "mode": "live",
            "budget_available": 30000.0,
        }
        mock_repo.list_reconcilable_live_buys.return_value = [
            {
                "trade_id": 7,
                "wolf_id": "W0015",
                "symbol": "INDUSINDBK",
                "quantity": 5,
                "price": 1000.0,
                "order_status": "pending",
                "kite_order_id": "999",
            }
        ]

        kite = MagicMock()
        kite.order_history.return_value = [{"status": "REJECTED"}]
        mock_kite_fn.return_value = kite

        result = reconcile_live_orders_for_wolf("W0015")

        self.assertEqual(len(result["rejected"]), 1)
        mock_repo.update_trade_order.assert_called_once_with(7, order_status="rejected")
        mock_repo.close_holding.assert_called_once_with("W0015", "INDUSINDBK")
        mock_repo.set_budget_available.assert_called_once_with("W0015", 35000.0)

    @patch("backend.kite_order_reconcile.repo")
    def test_skips_non_live_wolf(self, mock_repo):
        mock_repo.get_wolf.return_value = {"wolf_id": "W0001", "mode": "paper"}
        result = reconcile_live_orders_for_wolf("W0001")
        self.assertTrue(result.get("skipped"))
        mock_repo.list_reconcilable_live_buys.assert_not_called()


if __name__ == "__main__":
    unittest.main()
