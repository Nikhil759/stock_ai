"""Offline tests for deploy live price overlay."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from deploy.live_prices import apply_live_shortlist_prices, is_nse_market_open


class TestLiveShortlistPrices(unittest.TestCase):
    def test_market_hours_weekday_open(self):
        wed_10am = datetime(2026, 7, 15, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertTrue(is_nse_market_open(now=wed_10am))

    def test_market_hours_weekend_closed(self):
        sat = datetime(2026, 7, 18, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertFalse(is_nse_market_open(now=sat))

    def test_market_hours_before_open(self):
        early = datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertFalse(is_nse_market_open(now=early))

    @patch("deploy.live_prices._fetch_shortlist_ltps")
    def test_overlay_live_prices(self, mock_ltps):
        mock_ltps.return_value = {"INFY": 1850.5}
        shortlist = [{"symbol": "INFY", "price": 1800.0, "conviction": 80}]
        out = apply_live_shortlist_prices(
            shortlist,
            run_date=date(2026, 7, 15),
            force=True,
        )
        self.assertEqual(out[0]["price"], 1850.5)
        self.assertEqual(out[0]["price_morning"], 1800.0)

    def test_skip_overlay_when_closed(self):
        shortlist = [{"symbol": "INFY", "price": 1800.0}]
        with patch("deploy.live_prices.today_ist", return_value=date(2026, 7, 15)):
            with patch("deploy.live_prices.is_nse_market_open", return_value=False):
                out = apply_live_shortlist_prices(shortlist, run_date=date(2026, 7, 15))
        self.assertEqual(out[0]["price"], 1800.0)


if __name__ == "__main__":
    unittest.main()
