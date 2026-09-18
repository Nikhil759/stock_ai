"""Offline tests for Kite order helpers."""
from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from wolf_executor.kite_stub import kite_order_variety, live_orders_enabled, place_kite_order

IST = ZoneInfo("Asia/Kolkata")


class TestKiteOrderVariety(unittest.TestCase):
    def test_regular_during_session(self):
        dt = datetime(2026, 9, 18, 10, 30, tzinfo=IST)
        self.assertEqual(kite_order_variety(now=dt), "regular")

    def test_amo_before_open(self):
        dt = datetime(2026, 9, 18, 8, 55, tzinfo=IST)
        self.assertEqual(kite_order_variety(now=dt), "amo")

    def test_amo_after_close(self):
        dt = datetime(2026, 9, 18, 17, 0, tzinfo=IST)
        self.assertEqual(kite_order_variety(now=dt), "amo")

    def test_amo_on_weekend(self):
        dt = datetime(2026, 9, 19, 10, 0, tzinfo=IST)  # Saturday
        self.assertEqual(kite_order_variety(now=dt), "amo")


class TestLiveOrdersEnabled(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(live_orders_enabled())

    def test_enabled_with_env(self):
        with patch.dict(os.environ, {"WOLF_LIVE_ORDERS_ENABLED": "1"}):
            self.assertTrue(live_orders_enabled())


class TestPlaceKiteOrder(unittest.TestCase):
    def test_raises_when_disabled(self):
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "1"}, clear=True):
            with self.assertRaises(RuntimeError):
                place_kite_order(
                    wolf_id="W0001",
                    symbol="INFY",
                    action="BUY",
                    quantity=1,
                    price=100.0,
                )

    @patch("wolf_executor.kite_stub._get_kite")
    def test_places_limit_cnc_order(self, mock_get_kite):
        kite = MagicMock()
        kite.TRANSACTION_TYPE_BUY = "BUY"
        kite.TRANSACTION_TYPE_SELL = "SELL"
        kite.VARIETY_REGULAR = "regular"
        kite.VARIETY_AMO = "amo"
        kite.EXCHANGE_NSE = "NSE"
        kite.PRODUCT_CNC = "CNC"
        kite.ORDER_TYPE_LIMIT = "LIMIT"
        kite.place_order.return_value = "260918220543481"
        mock_get_kite.return_value = kite

        with patch.dict(os.environ, {"WOLF_LIVE_ORDERS_ENABLED": "1"}):
            order_id = place_kite_order(
                wolf_id="W0001",
                symbol="INFY",
                action="BUY",
                quantity=1,
                price=993.4,
            )

        self.assertEqual(order_id, "260918220543481")
        kite.place_order.assert_called_once()
        kwargs = kite.place_order.call_args.kwargs
        self.assertEqual(kwargs["tradingsymbol"], "INFY")
        self.assertEqual(kwargs["quantity"], 1)
        self.assertEqual(kwargs["price"], 993.4)


if __name__ == "__main__":
    unittest.main()
