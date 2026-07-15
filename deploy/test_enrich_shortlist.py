"""Offline tests for deploy shortlist dossier enrich."""
from __future__ import annotations

import unittest

from deploy.enrich_shortlist import enrich_shortlist_with_dossiers, slim_dossier_for_deploy


class TestEnrichShortlist(unittest.TestCase):
    def test_slim_dossier_trims_news(self):
        raw = {
            "meta": {"ticker": "INFY"},
            "fundamentals": {"price": 1800, "pe": 22},
            "technicals": {"rsi_14": 55},
            "news": {
                "aggregate_sentiment": "positive",
                "items": [
                    {"date": "d1", "headline": "h1", "sentiment_score": 0.2},
                    {"date": "d2", "headline": "h2", "sentiment_score": 0.1},
                    {"date": "d3", "headline": "h3", "sentiment_score": 0.0},
                    {"date": "d4", "headline": "h4", "sentiment_score": -0.1},
                ],
            },
            "order_book": {"bids": []},
        }
        slim = slim_dossier_for_deploy(raw)
        self.assertEqual(slim["fundamentals"]["pe"], 22)
        self.assertEqual(len(slim["news"]["items"]), 3)
        self.assertNotIn("order_book", slim)

    def test_enrich_attaches_dossier(self):
        shortlist = [
            {"symbol": "INFY", "price": 1850, "conviction": 80, "verdict": "buy"},
            {"symbol": "MISSING", "price": 100, "conviction": 50, "verdict": "watch"},
        ]
        index = {
            "INFY": {
                "meta": {"ticker": "INFY"},
                "fundamentals": {"price": 1800, "graham_number": 1600},
                "technicals": {"above_200dma": True},
            }
        }
        out = enrich_shortlist_with_dossiers(shortlist, dossier_index=index)
        self.assertIn("dossier", out[0])
        self.assertEqual(out[0]["dossier"]["fundamentals"]["graham_number"], 1600)
        self.assertNotIn("dossier", out[1])
        self.assertEqual(out[0]["price"], 1850)


if __name__ == "__main__":
    unittest.main()
